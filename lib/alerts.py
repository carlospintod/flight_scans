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
    calendar_history_for_itinerary,
    insert_alert_rows,
    latest_calendar_snapshot_per_itinerary,
)
from .searchapi_io import SOURCE_ID as SEARCHAPI_SOURCE

LOG = logging.getLogger(__name__)


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
    # don't conflate Sky Scrapper noise with SearchAPI baselines.
    latest = latest_calendar_snapshot_per_itinerary(conn, route.name)
    LOG.info("alerts route=%s latest_rows=%d", route.name, len(latest))

    new_alerts: list[AlertRow] = []
    for row in latest:
        if row["stay_days"] < min_stay or row["stay_days"] > max_stay:
            continue
        src = row["source"]
        history = calendar_history_for_itinerary(
            conn,
            route.name,
            row["origin"], row["destination"],
            row["departure_date"], row["return_date"],
            since=baseline_since,
            source=src,
        )
        prior = [r["price"] for r in history if r["snapshot_at"] < row["snapshot_at"]]
        if len(prior) < min_obs:
            continue
        median = statistics.median(prior)
        if median <= 0:
            continue
        drop = (median - row["price"]) / median * 100.0
        if drop < drop_pct:
            continue
        # Dedup: don't re-fire if we've already alerted on this exact
        # itinerary+source at this price (or lower) within the baseline
        # window. Without this, every evaluate() run re-appends an alert
        # for any itinerary still meeting the condition, so repeated runs
        # pile up dozens of duplicate rows for the same signal.
        if _already_alerted(
            conn, route.name, src,
            row["origin"], row["destination"],
            row["departure_date"], row["return_date"],
            price=row["price"], since=baseline_since,
        ):
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
        ))

    if new_alerts:
        insert_alert_rows(conn, new_alerts)
        _append_log(log_path, new_alerts)
        for a in new_alerts:
            print(
                f"ALERT {a.fired_at} [{a.source}] {a.origin}->{a.destination} "
                f"{a.departure_date}..{a.return_date} "
                f"{a.price} {a.currency} (median={a.baseline_median}, "
                f"-{a.drop_pct:.1f}%)"
            )
    return new_alerts


def _already_alerted(
    conn, route_id: str, source: str,
    origin: str, destination: str,
    departure_date: str, return_date: str,
    *, price: int, since: date,
) -> bool:
    """True if an alert already fired for this itinerary at <= this price
    within the baseline window. Prevents duplicate alerts on re-runs.

    We compare on price <= current so a genuine *further* drop still
    fires (it's new signal), but a flat or higher price on the same
    itinerary stays quiet.
    """
    row = conn.execute(
        """
        SELECT 1 FROM alerts
        WHERE route_id = ? AND source = ?
          AND origin = ? AND destination = ?
          AND departure_date = ? AND return_date = ?
          AND price <= ?
          AND fired_at >= ?
        LIMIT 1
        """,
        (route_id, source, origin, destination,
         departure_date, return_date, price, since.isoformat()),
    ).fetchone()
    return row is not None


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
