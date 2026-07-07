"""Alert evaluation.

A drop is interesting iff all three hold (per CLAUDE.md):

  1. stay_days in [stay.min_days, stay.max_days]
  2. price is at least `drop_threshold_pct` below the trailing
     `baseline_window_days` median for that exact itinerary
  3. we have at least `min_observations` prior snapshots inside the
     baseline window

The "current price" we evaluate against is the most-recent snapshot for
each itinerary. Each evaluation pass appends new rows to the `alerts`
table and to `data/alerts.log` (one line per alert).
"""

from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .config import RouteConfig
from .db import (
    AlertRow,
    insert_alert_rows,
    latest_calendar_snapshot_per_itinerary,
)

LOG = logging.getLogger(__name__)

# (source, origin, destination, departure_date, return_date)
_ItinKey = tuple


def _batch_prev_min(conn, route_id: str) -> dict[_ItinKey, int]:
    """All-time minimum per (source, itinerary) STRICTLY BEFORE that
    group's latest snapshot — the new_low reference. One query instead of
    one per row: on the Turso HTTP backend each query is a network round
    trip, and a kiwi-discovered search has hundreds of itineraries
    (red-team A8: ~900 round-trips per search on the per-row version).
    """
    out: dict[_ItinKey, int] = {}
    for r in conn.execute(
        """
        SELECT cs.source, cs.origin, cs.destination,
               cs.departure_date, cs.return_date,
               MIN(cs.price) AS prev_min
        FROM calendar_snapshots cs
        JOIN (
            SELECT source, origin, destination, departure_date, return_date,
                   MAX(snapshot_at) AS latest
            FROM calendar_snapshots
            WHERE route_id = ?
            GROUP BY source, origin, destination, departure_date, return_date
        ) m
          ON m.source = cs.source AND m.origin = cs.origin
         AND m.destination = cs.destination
         AND m.departure_date = cs.departure_date
         AND m.return_date = cs.return_date
        WHERE cs.route_id = ? AND cs.snapshot_at < m.latest
        GROUP BY cs.source, cs.origin, cs.destination,
                 cs.departure_date, cs.return_date
        """,
        (route_id, route_id),
    ).fetchall():
        out[(r["source"], r["origin"], r["destination"],
             r["departure_date"], r["return_date"])] = r["prev_min"]
    return out


def _batch_window_history(conn, route_id: str, since_iso: str,
                          ) -> dict[_ItinKey, list[tuple[str, int]]]:
    """(snapshot_at, price) per (source, itinerary) inside the baseline
    window, oldest-first — the drop-rule inputs, one query for all rows."""
    out: dict[_ItinKey, list[tuple[str, int]]] = {}
    for r in conn.execute(
        """
        SELECT source, origin, destination, departure_date, return_date,
               snapshot_at, price
        FROM calendar_snapshots
        WHERE route_id = ? AND snapshot_at >= ?
        ORDER BY snapshot_at ASC
        """,
        (route_id, since_iso),
    ).fetchall():
        key = (r["source"], r["origin"], r["destination"],
               r["departure_date"], r["return_date"])
        out.setdefault(key, []).append((r["snapshot_at"], r["price"]))
    return out


def _batch_alerted_min(conn, route_id: str, since_iso: str,
                       ) -> dict[_ItinKey, int]:
    """Lowest already-fired alert price per (source, itinerary) inside the
    window. `min_alerted <= current` is exactly the old per-row
    EXISTS(price <= current) dedupe."""
    out: dict[_ItinKey, int] = {}
    for r in conn.execute(
        """
        SELECT source, origin, destination, departure_date, return_date,
               MIN(price) AS min_alerted
        FROM alerts
        WHERE route_id = ? AND fired_at >= ?
        GROUP BY source, origin, destination, departure_date, return_date
        """,
        (route_id, since_iso),
    ).fetchall():
        out[(r["source"], r["origin"], r["destination"],
             r["departure_date"], r["return_date"])] = r["min_alerted"]
    return out


def evaluate(
    *,
    conn,
    route: RouteConfig,
    log_path: Path,
    today: date | None = None,
) -> list[AlertRow]:
    today = today or date.today()
    baseline_since = today - timedelta(days=route.alerts.baseline_window_days)
    min_stay = route.stay.min_days
    max_stay = route.stay.max_days
    drop_pct = route.alerts.drop_threshold_pct
    min_obs = route.alerts.min_observations
    fired_at = _now_iso()

    # One row per (source, itinerary). Alerts fire per-source so we
    # don't conflate one source's noise with another's baselines.
    latest = latest_calendar_snapshot_per_itinerary(conn, route.name)
    LOG.info("alerts route=%s latest_rows=%d", route.name, len(latest))

    # Batched pulls (3 queries total) replacing the per-row lookups —
    # identical semantics, hundreds fewer Turso round-trips (A8).
    prev_min_map = _batch_prev_min(conn, route.name)
    history_map = _batch_window_history(conn, route.name,
                                        baseline_since.isoformat())
    alerted_min_map = _batch_alerted_min(conn, route.name,
                                         baseline_since.isoformat())

    # Alerts must respect the CURRENT search window — the DB keeps
    # out-of-window history (by design), but a great fare you can't
    # take is not an alert. Same filter the followup applies.
    sw = route.search_window
    latest_dep_allowed = sw.latest_return - timedelta(days=min_stay)

    new_alerts: list[AlertRow] = []
    fired_keys: set[tuple] = set()  # itinerary+source fired in THIS pass
    for row in latest:
        if row["stay_days"] < min_stay or row["stay_days"] > max_stay:
            continue
        try:
            dep_d = date.fromisoformat(row["departure_date"])
            ret_d = date.fromisoformat(row["return_date"])
        except (ValueError, TypeError):
            continue
        if (dep_d < sw.earliest_departure or dep_d > latest_dep_allowed
                or ret_d > sw.latest_return):
            continue
        src = row["source"]
        itin_key = (src, row["origin"], row["destination"],
                    row["departure_date"], row["return_date"])

        def _already_alerted_batched(price: int) -> bool:
            """A prior alert at <= this price inside the window mutes it;
            a genuine FURTHER drop still fires (new signal)."""
            min_alerted = alerted_min_map.get(itin_key)
            return min_alerted is not None and min_alerted <= price

        # --- NEW-LOW alert: latest price strictly below the previous
        # all-time minimum for this itinerary+source. Needs only ONE
        # prior observation, so it fires from the second scan onward —
        # the right alert mode for a near-in booking window where the
        # median-drop rule wouldn't accumulate enough history in time.
        #
        # Interestingness bar: when the route configures a followup
        # watch price, only prices AT OR BELOW it can fire a new_low.
        # Without the bar, a 900->880 twitch is technically a new low
        # and one sweep of fresh data floods the table (403 alerts in
        # one pass, observed 2026-07-05).
        new_low_bar = route.followup.watch_below_price
        if new_low_bar is not None and row["price"] > new_low_bar:
            prev_min = None
        else:
            prev_min = prev_min_map.get(itin_key)
        # itin_key in fired_keys: duplicate same-second snapshot rows make
        # latest_calendar_snapshot_per_itinerary yield the SAME itinerary
        # twice, and this branch fired once per duplicate (8 doubled
        # alerts in production, found 2026-07-07). The drop branch below
        # always had this check; new_low was missing it.
        if (prev_min is not None and row["price"] < prev_min
                and itin_key not in fired_keys):
            if not _already_alerted_batched(row["price"]):
                new_alerts.append(AlertRow(
                    fired_at=fired_at,
                    route_id=route.name,
                    source=src,
                    origin=row["origin"],
                    destination=row["destination"],
                    departure_date=row["departure_date"],
                    return_date=row["return_date"],
                    price=row["price"],
                    currency=row["currency"],
                    baseline_median=int(prev_min),
                    drop_pct=round(
                        (prev_min - row["price"]) / prev_min * 100.0, 2
                    ) if prev_min > 0 else 0.0,
                    alert_type="new_low",
                ))
                fired_keys.add(itin_key)
        history = history_map.get(itin_key, [])
        prior = [price for snap_at, price in history
                 if snap_at < row["snapshot_at"]]
        if len(prior) < min_obs:
            continue
        median = statistics.median(prior)
        if median <= 0:
            continue
        drop = (median - row["price"]) / median * 100.0
        if drop < drop_pct:
            continue
        # Dedup: skip if this itinerary already fired in THIS pass (as a
        # new_low), or if a prior run alerted at this price or lower
        # inside the baseline window. Without the DB check, every
        # evaluate() re-appended an alert for any itinerary still meeting
        # the condition — 76 duplicate rows from 3 signals, once.
        if itin_key in fired_keys:
            continue
        if _already_alerted_batched(row["price"]):
            continue
        new_alerts.append(AlertRow(
            fired_at=fired_at,
            route_id=route.name,
            source=src,
            origin=row["origin"],
            destination=row["destination"],
            departure_date=row["departure_date"],
            return_date=row["return_date"],
            price=row["price"],
            currency=row["currency"],
            baseline_median=int(round(median)),
            drop_pct=round(drop, 2),
            alert_type="drop",
        ))
        fired_keys.add(itin_key)

    if new_alerts:
        insert_alert_rows(conn, new_alerts)
        _append_log(log_path, new_alerts)
        for a in new_alerts:
            print(
                f"ALERT[{a.alert_type}] {a.fired_at} [{a.source}] "
                f"{a.origin}->{a.destination} "
                f"{a.departure_date}..{a.return_date} "
                f"{a.price} {a.currency} (ref={a.baseline_median}, "
                f"-{a.drop_pct:.1f}%)"
            )
    return new_alerts


def _append_log(log_path: Path, alerts: Iterable[AlertRow]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        for a in alerts:
            f.write(
                f"{a.fired_at}\troute={a.route_id}\tsource={a.source}\t"
                f"{a.origin}->{a.destination}\t"
                f"dep={a.departure_date}\tret={a.return_date}\t"
                f"price={a.price}\tccy={a.currency}\t"
                f"median={a.baseline_median}\tdrop_pct={a.drop_pct:.2f}\n"
            )


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
