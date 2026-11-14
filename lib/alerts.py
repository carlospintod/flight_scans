"""Alert evaluation: three alert types, one pass per search per scan.

* `drop`, per (source, itinerary). Interesting iff all three hold (per
  CLAUDE.md):

    1. stay_days in [stay.min_days, stay.max_days]
    2. price is at least `drop_threshold_pct` below the trailing
       `baseline_window_days` median for that exact itinerary
    3. we have at least `min_observations` prior snapshots inside the
       baseline window

* `new_low`, per (source, itinerary): the latest price beats that
  itinerary's previous all-time minimum (one prior row is enough).

* `window_best`, per search (2026-11-14): a verified fare stored by this
  scan beats every verified fare the search has ever seen for a trip in
  its current window. The two rules above need an earlier row from the
  same source for the same trip, so the scraper's FIRST look at a trip
  the calendar sweep nominated could never alert, even at the cheapest
  price the search had seen (NBO audit F4). See `_window_best`.

Every rule only looks at trips inside the current search window. The
"current price" for drop and new_low is the most-recent snapshot for each
itinerary. Each evaluation pass appends new rows to the `alerts` table and
to `data/alerts.log` (one line per alert).
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
from .googleflights_direct import SOURCE_ID as GOOGLEFLIGHTS_SOURCE_ID
from .serpapi_io import CALENDAR_SOURCE_ID
from .serpapi_io import SOURCE_ID as SERPAPI_SOURCE_ID

LOG = logging.getLogger(__name__)

# Row labels that NOMINATE but never alert on their own (2026-11-14).
# Calendar-sweep cells are Google's date-grid price for ~4,000 pairs at
# once, re-priced every two weeks: comparing one sweep with the next would
# fire a new_low for every pair that slipped 1% under the watch bar — the
# 403-alerts-in-one-pass flood of 2026-07-05, at sweep scale. Their
# cheapest pairs reach the free scraper through select_candidates, and
# the scraper's verified rows alert exactly as before.
NOMINATION_ONLY_SOURCES = frozenset({CALENDAR_SOURCE_ID})

# A new all-time low must beat the previous minimum by at least this much
# to be worth a notification. Without it, a 567 -> 566 render twitch is
# "a new low" and fires (4 such 1-EUR alerts in the 2026-07-07 CI batch).
# max(5 EUR, 1%): kills parse jitter, keeps genuine moves.
NEW_LOW_MIN_ABS = 5
NEW_LOW_MIN_PCT = 0.01

WINDOW_BEST = "window_best"

# Row labels whose rows are a LIVE priced itinerary from Google's corpus:
# the free scraper and SerpApi point queries (the discovery grid, the
# verification fallback, the OTA seller price). Only these set the window
# best or can beat it (Carlos, 2026-11-14). It is an allowlist, not "all
# but the leads", so a new rail cannot start paging by accident. Left out:
#   * the leads: serpapi_calendar cells and aviasales cached quotes
#     (followup.LEAD_SOURCES);
#   * searchapi: its rectangle-sweep cells are the same Google date grid
#     as serpapi_calendar, and its break-glass point queries carry the
#     same label, so the two cannot be told apart in calendar_snapshots;
#   * kiwi, skyscanner, flights_sky: other corpora, retired or idle.
WINDOW_BEST_SOURCES = frozenset({GOOGLEFLIGHTS_SOURCE_ID, SERPAPI_SOURCE_ID})

# (source, origin, destination, departure_date, return_date)
_ItinKey = tuple
# (origin, destination, departure_date, return_date): the trip, any source
_TripKey = tuple


def _is_meaningful_new_low(prev_min: int, price: int) -> bool:
    """True when `price` beats `prev_min` by the interestingness epsilon."""
    if price >= prev_min:
        return False
    drop = prev_min - price
    return drop >= max(NEW_LOW_MIN_ABS, prev_min * NEW_LOW_MIN_PCT)


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


def _trip_in_window(route: RouteConfig, departure_date: str,
                    return_date: str, stay_days: int | None) -> bool:
    """True when the trip fits the route's CURRENT search window and stay
    range. The DB keeps out-of-window history (by design), but a great
    fare you can't take is not an alert. Same filter the followup applies.
    One-way trips carry the '' return sentinel and no stay."""
    one_way = route.is_one_way
    if not one_way and (stay_days is None
                        or stay_days < route.stay.min_days
                        or stay_days > route.stay.max_days):
        return False
    try:
        dep_d = date.fromisoformat(departure_date)
        ret_d = None if one_way else date.fromisoformat(return_date)
    except (ValueError, TypeError):
        return False
    sw = route.search_window
    # One-way: latest departure is the window end itself (no return leg
    # to fit); round-trip: latest_return - min_stay. Both capped by
    # latest_departure when set.
    latest_dep = sw.last_departure(0 if one_way else route.stay.min_days)
    if dep_d < sw.earliest_departure or dep_d > latest_dep:
        return False
    if ret_d is not None and ret_d > sw.latest_return:
        return False
    return True


def _stay_days(departure_date: str, return_date: str) -> int | None:
    """Stay length from the dates (the alerts table has no stay_days
    column): 0 for the one-way '' sentinel, None when unparseable."""
    if not return_date:
        return 0
    try:
        return (date.fromisoformat(return_date)
                - date.fromisoformat(departure_date)).days
    except (ValueError, TypeError):
        return None


def _window_best_alerts(conn, route_id: str) -> list:
    """Every window_best alert this search has fired. Few rows by
    construction: one per new record."""
    return conn.execute(
        """
        SELECT origin, destination, departure_date, return_date, price,
               fired_at
        FROM alerts
        WHERE route_id = ? AND alert_type = ?
        """,
        (route_id, WINDOW_BEST),
    ).fetchall()


def _window_best(conn, route: RouteConfig, *, since: str, fired_at: str,
                 prior_alerts: list) -> AlertRow | None:
    """This scan's window_best alert, or None.

    The rule (Carlos, 2026-11-14):
      * Only verified rows count (WINDOW_BEST_SOURCES), both for the bar
        and for the fare that beats it. A calendar cell or an Aviasales
        quote never fires it and never sets the bar, so the scraper
        verifying a cheap cell is exactly what fires.
      * The bar is the cheapest verified fare EVER seen (no lookback) for
        a trip inside the current window and stay range. Only in-window
        trips count, so the bar resets by itself when the window moves.
      * The fare must be stored by this scan (snapshot_at >= since), be
        below the bar by any amount (no epsilon), and be at or under the
        route's watch bar (watch_below_price) when one is set.
      * At most one per scan: the cheapest such fare.

    A re-check at the same price never fires: the earlier row is part of
    the bar, and the bar must be beaten. The first verified fare in an
    empty window does not fire either, because there is nothing to beat.
    `prior_alerts` stops a second evaluate() over the same scan from
    firing the same record twice.
    """
    sw = route.search_window
    latest_dep = sw.last_departure(
        0 if route.is_one_way else route.stay.min_days)
    sources = sorted(WINDOW_BEST_SOURCES)
    marks = ",".join("?" * len(sources))
    rows = conn.execute(
        f"""
        SELECT snapshot_at, source, origin, destination, departure_date,
               return_date, stay_days, price, currency
        FROM calendar_snapshots
        WHERE route_id = ? AND source IN ({marks})
          AND departure_date >= ? AND departure_date <= ?
        """,
        (route.name, *sources, sw.earliest_departure.isoformat(),
         latest_dep.isoformat()),
    ).fetchall()

    bar: int | None = None   # cheapest verified in-window fare before `since`
    fresh = []               # verified in-window rows this scan stored
    for r in rows:
        # Defensive: a zero price would become a permanent bar and mute
        # the rule for good; another currency is not comparable.
        if r["price"] <= 0 or r["currency"] != route.currency:
            continue
        if not _trip_in_window(route, r["departure_date"], r["return_date"],
                               r["stay_days"]):
            continue
        if r["snapshot_at"] < since:
            if bar is None or r["price"] < bar:
                bar = r["price"]
        else:
            fresh.append(r)
    if bar is None or not fresh:
        return None

    best = min(fresh, key=lambda r: (
        r["price"], r["snapshot_at"], r["source"], r["origin"],
        r["destination"], r["departure_date"], r["return_date"]))
    price = best["price"]
    if price >= bar:
        return None
    watch = route.followup.watch_below_price
    if watch is not None and price > watch:
        return None
    for a in prior_alerts:
        if a["price"] <= price and _trip_in_window(
                route, a["departure_date"], a["return_date"],
                _stay_days(a["departure_date"], a["return_date"])):
            return None
    return AlertRow(
        fired_at=fired_at,
        route_id=route.name,
        source=best["source"],
        origin=best["origin"],
        destination=best["destination"],
        departure_date=best["departure_date"],
        return_date=best["return_date"],
        price=price,
        currency=best["currency"],
        baseline_median=int(bar),   # the reference: the previous best
        drop_pct=round((bar - price) / bar * 100.0, 2),
        alert_type=WINDOW_BEST,
    )


def evaluate(
    *,
    conn,
    route: RouteConfig,
    log_path: Path,
    today: date | None = None,
    since: str | None = None,
) -> list[AlertRow]:
    """Run every alert rule for one search and record what fired.

    `since` is when this scan started, in the snapshot_at format (ISO UTC,
    'Z'). Rows at or after it are this scan's. The window_best rule needs
    it to tell this scan's fares from the bar they must beat, so it is
    skipped when `since` is None (callers outside lib.runner).
    """
    today = today or date.today()
    baseline_since = today - timedelta(days=route.alerts.baseline_window_days)
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

    new_alerts: list[AlertRow] = []
    fired_keys: set[tuple] = set()  # itinerary+source fired in THIS pass

    # --- WINDOW-BEST alert: one per scan, decided first. It names a new
    # record for the whole search, so it replaces a new_low/drop for the
    # same trip: one push line per trip, not two.
    wb_history = _window_best_alerts(conn, route.name)
    wb_trips: set[_TripKey] = set()   # trips window_best fired in THIS pass
    if since is not None:
        wb = _window_best(conn, route, since=since, fired_at=fired_at,
                          prior_alerts=wb_history)
        if wb is not None:
            new_alerts.append(wb)
            wb_trips.add((wb.origin, wb.destination,
                          wb.departure_date, wb.return_date))
    # A past window_best alert also mutes new_low/drop for the same trip
    # from ANY source at or above its price, inside the baseline window:
    # the per-(source, itinerary) dedup below, widened to the trip because
    # window_best is per search. Without it, a second source's row for the
    # trip skipped in this pass would fire on the next scan.
    wb_trip_min: dict[_TripKey, int] = {}
    for a in wb_history:
        if a["fired_at"] >= baseline_since.isoformat():
            k = (a["origin"], a["destination"],
                 a["departure_date"], a["return_date"])
            wb_trip_min[k] = min(a["price"], wb_trip_min.get(k, a["price"]))

    for row in latest:
        if row["source"] in NOMINATION_ONLY_SOURCES:
            continue
        if not _trip_in_window(route, row["departure_date"],
                               row["return_date"], row["stay_days"]):
            continue
        src = row["source"]
        itin_key = (src, row["origin"], row["destination"],
                    row["departure_date"], row["return_date"])
        trip_key = itin_key[1:]
        if trip_key in wb_trips:   # window_best already covers this trip
            continue

        def _already_alerted_batched(price: int) -> bool:
            """A prior alert at <= this price inside the window mutes it;
            a genuine FURTHER drop still fires (new signal)."""
            min_alerted = alerted_min_map.get(itin_key)
            if min_alerted is not None and min_alerted <= price:
                return True
            wb_min = wb_trip_min.get(trip_key)
            return wb_min is not None and wb_min <= price

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
        if (prev_min is not None
                and _is_meaningful_new_low(prev_min, row["price"])
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


def summary_entry(a: AlertRow) -> dict:
    """One alert as run_batch and run_scan write it into the JSON summary
    that scripts/notify_ntfy.py turns into the phone push. `ref_price` is
    the price the alert was judged against: the trailing median (drop),
    the trip's previous low (new_low) or the window's previous best
    (window_best)."""
    return {"type": a.alert_type, "origin": a.origin,
            "destination": a.destination,
            "departure_date": a.departure_date,
            "return_date": a.return_date,
            "price": a.price, "currency": a.currency,
            "ref_price": a.baseline_median}


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
