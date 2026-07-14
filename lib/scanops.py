"""Scan execution steps shared by the CLI and the UI.

These used to live in ui/_common.py, which imports Streamlit at module
level — so the "headless" run_scan.py silently required Streamlit and
could never run on a CI runner. They contain no UI code; only the module
they lived in did. Moved verbatim 2026-07-06.

Each function executes one RunPlan slice against one client and persists
rows; all take `dry_run` and return the number of rows stored.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

LOG = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enrich_ota_sellers(conn, serpapi_client, route, *, since: str) -> int:
    """For the cheapest FRESH live itinerary this scan, ask SerpApi
    booking_options which sellers Google lists (airlines AND OTAs like
    Gotogate/Mytrip) — the cheapest is often an OTA BELOW the headline
    price. If it beats the verified price, store it as a fresh
    observation (so it feeds "cheapest now" + alerts) with the seller.

    The reliable OTA-coverage win (2026-07-14): no captcha, no new
    subscription — it rides Google's own index. Best-effort and BOUNDED:
    it spends from the search's ALREADY-RESERVED serpapi contingency
    budget (GuardedClient enforces it), so actual serpapi spend still
    never exceeds the quote. Returns rows stored.
    """
    if serpapi_client is None:
        return 0
    from .db import CalendarRow, PointRow, insert_calendar_rows, insert_point_rows
    from .serpapi_io import top_booking_token
    # Cheapest fresh LIVE observation (exclude cached aviasales leads).
    row = conn.execute(
        "SELECT origin, destination, departure_date, return_date, "
        "stay_days, price FROM calendar_snapshots WHERE route_id = ? "
        "AND snapshot_at >= ? AND source != 'aviasales' "
        "ORDER BY price ASC LIMIT 1",
        (route.name, since)).fetchone()
    if row is None:
        return 0
    try:
        dep = date.fromisoformat(row["departure_date"])
        ret = (date.fromisoformat(row["return_date"])
               if row["return_date"] else None)
    except (ValueError, TypeError):
        return 0
    try:
        resp = serpapi_client.point_query(
            origin=row["origin"], destination=row["destination"],
            outbound=dep, return_=ret, currency=route.currency)
        token = top_booking_token(resp)
        if not token:
            return 0
        sellers = serpapi_client.booking_options(
            booking_token=token, origin=row["origin"],
            destination=row["destination"], outbound=dep, return_=ret,
            currency=route.currency)
    except Exception as exc:  # noqa: BLE001 — incl. QuotaExceeded (budget spent)
        LOG.info("ota enrich %s->%s skipped: %s",
                 row["origin"], row["destination"], exc)
        return 0
    if not sellers:
        return 0
    best = sellers[0]                      # parsed cheapest-first
    if best.price >= row["price"]:
        return 0                           # no improvement over verified price
    carriers = (resp.best_flights[0].carriers
                if resp.best_flights else "")
    snapshot_at = _now_iso()
    stored = insert_calendar_rows(conn, [CalendarRow(
        snapshot_at=snapshot_at, route_id=route.name, source="serpapi",
        origin=row["origin"], destination=row["destination"],
        departure_date=row["departure_date"], return_date=row["return_date"],
        stay_days=row["stay_days"], price=best.price,
        currency=best.currency, is_lowest_price=False)])
    insert_point_rows(conn, [PointRow(
        snapshot_at=snapshot_at, route_id=route.name, source="serpapi",
        origin=row["origin"], destination=row["destination"],
        departure_date=row["departure_date"], return_date=row["return_date"],
        rank=0, price=best.price, currency=best.currency, carriers=carriers,
        total_minutes=None, stops=None,
        is_self_transfer=best.separate_tickets, seller=best.book_with)])
    LOG.info("ota enrich: %s @ %d %s (below verified %d) %s->%s",
             best.book_with, best.price, best.currency, row["price"],
             row["origin"], row["destination"])
    return stored


def run_aviasales_sweep(conn, av_client, route, *, dry_run: bool,
                        pairs: list | None = None,
                        months: list | None = None) -> int:
    """Aviasales cached sweep; persist rows.

    Round-trip: one cheap_prices call per (origin, destination).
    One-way routes: one one_way_month_prices call per (pair, month) —
    `months` is the RunPlan's "YYYY-MM" list (quote == execution), and
    rows land with the '' return sentinel / stay_days=0.

    `pairs`: explicit (origin, destination) list from the RunPlan. When
    None, defaults to the full route cross-product (legacy behavior).

    Returns the number of rows stored (0 in dry-run).
    """
    if dry_run or av_client is None:
        return 0
    from .aviasales_api import AviasalesError, SOURCE_ID as AV_SOURCE
    from .db import CalendarRow, insert_calendar_rows
    snapshot_at = _now_iso()
    stored = 0
    if pairs is None:
        pairs = [(o, d) for o in route.origins for d in route.destinations]
    if route.is_one_way:
        for origin, destination in pairs:
            for month in (months or []):
                try:
                    resp = av_client.one_way_month_prices(
                        origin=origin, destination=destination,
                        month=month, currency=route.currency,
                    )
                except AviasalesError as exc:
                    LOG.warning("aviasales one-way %s->%s %s err=%s",
                                origin, destination, month, exc)
                    continue
                rows = []
                for q in resp.quotes:
                    if q.return_date:
                        continue  # one-way cache only
                    try:
                        date.fromisoformat(q.departure_date)
                    except (ValueError, TypeError):
                        continue
                    rows.append(CalendarRow(
                        snapshot_at=snapshot_at,
                        route_id=route.name,
                        source=AV_SOURCE,
                        origin=q.origin or origin,
                        destination=q.destination or destination,
                        departure_date=q.departure_date,
                        return_date="",
                        stay_days=0,
                        price=q.price,
                        currency=q.currency or route.currency,
                        is_lowest_price=False,
                    ))
                stored += insert_calendar_rows(conn, rows)
                LOG.info("aviasales one-way sweep %s->%s %s rows=%d",
                         origin, destination, month, len(rows))
        return stored
    for origin, destination in pairs:
        try:
            resp = av_client.cheap_prices(
                origin=origin, destination=destination,
                depart_date=None, return_date=None,
                currency=route.currency,
            )
        except AviasalesError as exc:
            LOG.warning("aviasales %s->%s err=%s", origin, destination, exc)
            continue
        rows: list[CalendarRow] = []
        for q in resp.quotes:
            if not q.return_date:
                continue  # /v1/prices/cheap is round-trip cache only
            try:
                d_dep = date.fromisoformat(q.departure_date)
                d_ret = date.fromisoformat(q.return_date)
            except (ValueError, TypeError):
                continue
            stay_days = (d_ret - d_dep).days
            if stay_days <= 0:
                continue
            rows.append(CalendarRow(
                snapshot_at=snapshot_at,
                route_id=route.name,
                source=AV_SOURCE,
                origin=q.origin or origin,
                destination=q.destination or destination,
                departure_date=q.departure_date,
                return_date=q.return_date,
                stay_days=stay_days,
                price=q.price,
                currency=q.currency or route.currency,
                is_lowest_price=False,
            ))
        stored += insert_calendar_rows(conn, rows)
        LOG.info("aviasales sweep %s->%s rows=%d", origin, destination, len(rows))
    return stored


def run_kiwi_discovery(conn, kw_client, route, *, bands, dry_run: bool) -> int:
    """Execute the plan's Kiwi range-search bands; persist all results.

    Each band = ONE Kiwi call returning the cheapest ~50 itineraries
    across a multi-week departure window — price + exact dates + carriers
    + virtual-interlining flag. Results land in BOTH calendar_snapshots
    (grid discovery) and point_queries (carrier detail), tagged 'kiwi'.
    Returns rows stored.
    """
    if dry_run or kw_client is None or not bands:
        return 0
    from .db import CalendarRow, PointRow, insert_calendar_rows, insert_point_rows
    from .kiwi_rapidapi import KiwiError, SOURCE_ID as KW_SOURCE
    snapshot_at = _now_iso()
    stored = 0
    one_way = route.is_one_way
    for b in bands:
        try:
            if one_way:
                resp = kw_client.one_way_range_search(
                    origin=b.origin, destination=b.destination,
                    outbound_start=b.outbound_start, outbound_end=b.outbound_end,
                    currency=route.currency, limit=50,
                )
            else:
                resp = kw_client.range_search(
                    origin=b.origin, destination=b.destination,
                    outbound_start=b.outbound_start, outbound_end=b.outbound_end,
                    inbound_start=b.inbound_start, inbound_end=b.inbound_end,
                    currency=route.currency, limit=50,
                )
        except KiwiError as exc:
            # A monthly-quota 429 fails identically for every band —
            # one clear line, stop the discovery pass (observed
            # 2026-07-06: 8 bands -> 8 error stanzas for one fact).
            if "MONTHLY quota" in str(exc) or "429" in str(exc):
                LOG.warning(
                    "kiwi monthly quota exhausted — skipping discovery "
                    "(resets ~10th); %d band(s) not attempted",
                    len(bands) - bands.index(b) - 1,
                )
                break
            LOG.error("kiwi band failed %s->%s %s..%s err=%s",
                      b.origin, b.destination, b.outbound_start,
                      b.outbound_end, exc)
            continue
        cal_rows, pq_rows = [], []
        for opt in resp.options:
            try:
                dep_d = date.fromisoformat(opt.depart_date)
            except (ValueError, TypeError):
                continue
            if one_way:
                # Sentinel: return_date='' and stay_days=0 make one-way
                # rows invisible to round-trip stay-range filters
                # (min_days>=1) while sharing the same tables.
                ret_str, stay = "", 0
            else:
                if not opt.return_date:
                    continue
                try:
                    ret_d = date.fromisoformat(opt.return_date)
                except (ValueError, TypeError):
                    continue
                stay = (ret_d - dep_d).days
                if stay <= 0:
                    continue
                ret_str = opt.return_date
            cal_rows.append(CalendarRow(
                snapshot_at=snapshot_at, route_id=route.name, source=KW_SOURCE,
                origin=b.origin, destination=b.destination,
                departure_date=opt.depart_date, return_date=ret_str,
                stay_days=stay, price=opt.price,
                currency=opt.currency or route.currency,
                is_lowest_price=False,
            ))
            pq_rows.append(PointRow(
                snapshot_at=snapshot_at, route_id=route.name, source=KW_SOURCE,
                origin=b.origin, destination=b.destination,
                departure_date=opt.depart_date, return_date=ret_str,
                rank=0, price=opt.price,
                currency=opt.currency or route.currency,
                carriers=opt.carriers, total_minutes=opt.total_minutes,
                stops=opt.stops, is_self_transfer=opt.is_virtual_interlining,
            ))
        stored += insert_calendar_rows(conn, cal_rows)
        insert_point_rows(conn, pq_rows)
        LOG.info("kiwi band %s->%s %s..%s: %d itineraries",
                 b.origin, b.destination, b.outbound_start, b.outbound_end,
                 len(cal_rows))
    return stored


def run_kiwi_followup(conn, kw_client, route, *, dry_run: bool,
                      max_calls: int = 20, candidates: list | None = None) -> int:
    """For each follow-up candidate, run one Kiwi round-trip search.

    Stores top results in `point_queries` tagged source='kiwi', with
    `is_self_transfer` set to True when Kiwi flagged virtual interlining.
    Returns rows stored.

    `candidates`: explicit list from the RunPlan (already window-filtered,
    diversified, and capped). When None, self-selects and applies the
    `max_calls` cap (legacy behavior).
    """
    if dry_run or kw_client is None:
        return 0
    from .db import PointRow, insert_point_rows
    from .followup import select_candidates
    from .kiwi_rapidapi import KiwiError, SOURCE_ID as KW_SOURCE
    if candidates is None:
        candidates = select_candidates(conn, route)
        if len(candidates) > max_calls:
            LOG.info("kiwi followup capping %d candidates to %d calls",
                     len(candidates), max_calls)
            candidates = candidates[:max_calls]
    snapshot_at = _now_iso()
    stored = 0
    MAX_RANKS = 3
    for c in candidates:
        # B3-style guard: this is a ROUND-TRIP search — a one-way ''
        # sentinel (or any malformed date) must skip the candidate, not
        # crash the batch. Normally unreachable (the planner never
        # routes one-way candidates here), but cheap insurance.
        try:
            dep_d = date.fromisoformat(c["departure_date"])
            ret_d = date.fromisoformat(c["return_date"])
        except (ValueError, TypeError, KeyError) as exc:
            LOG.warning("kiwi followup skipping candidate with unusable "
                        "dates dep=%r ret=%r: %s",
                        c.get("departure_date"), c.get("return_date"), exc)
            continue
        try:
            resp = kw_client.round_trip_search(
                origin=c["origin"], destination=c["destination"],
                depart_date=dep_d,
                return_date=ret_d,
                currency=route.currency,
            )
        except KiwiError as exc:
            LOG.warning(
                "kiwi %s->%s dep=%s ret=%s err=%s",
                c["origin"], c["destination"],
                c["departure_date"], c["return_date"], exc,
            )
            continue
        rows: list[PointRow] = []
        for i, opt in enumerate(resp.options[:MAX_RANKS]):
            rows.append(PointRow(
                snapshot_at=snapshot_at,
                route_id=route.name,
                source=KW_SOURCE,
                origin=c["origin"],
                destination=c["destination"],
                departure_date=c["departure_date"],
                return_date=c["return_date"],
                rank=i,
                price=opt.price,
                currency=opt.currency or route.currency,
                carriers=opt.carriers,
                total_minutes=opt.total_minutes,
                stops=opt.stops,
                is_self_transfer=opt.is_virtual_interlining,
            ))
        stored += insert_point_rows(conn, rows)
        LOG.info(
            "kiwi point %s->%s dep=%s ret=%s ranks=%d vi=%s",
            c["origin"], c["destination"],
            c["departure_date"], c["return_date"], len(rows),
            any(r.is_self_transfer for r in rows),
        )
    return stored
