"""Tier 1: calendar sweeps with full stay-range coverage.

Geometry (the important part). The calendar API caps at 200
(departure x return) combinations per call. We want every stay length
in [min_stay, max_stay] covered for every departure day in the search
window, using as few calls as possible.

For a rectangle with outbound width W_o (days d0..d0+W_o-1), covering
all stays [s1, s2] requires the return window to span
[d0 + s1, (d0+W_o-1) + s2] — width W_o + span - 1 where span = s2-s1+1.
The combo count is W_o * (W_o + span - 1), which must be <= 200:

    W_o = floor( (-(span-1) + sqrt((span-1)^2 + 800)) / 2 )

For span=31 (stay 60-90) that gives W_o=5 (5*35=175 combos). Rectangles
tile the departure axis with NO overlap (step = W_o), so each departure
day lands in exactly one rectangle and no combo is queried twice.
`sweep.overlap_days` is obsolete under this scheme.

Windows are clamped to today (no past departures) and to Google's ~330
day booking horizon (queries past it return no flights and waste calls).
Each clamp emits a human-readable note.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Iterator

from .config import RouteConfig
from .db import (
    CalendarRow,
    CurveRow,
    calendar_sweep_log,
    insert_calendar_rows,
    insert_calendar_sweep,
    insert_curve_rows,
)
from .searchapi_io import CalendarEntry, SearchApiClient, SearchApiError, SOURCE_ID as SEARCHAPI_SOURCE
from .serpapi_io import CALENDAR_SOURCE_ID as SERPAPI_CALENDAR_SOURCE, SerpApiError
from .skyscanner_rapidapi import (
    SkyScrapperClient,
    SkyScrapperError,
    SOURCE_ID as SKYSCANNER_SOURCE,
)

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepWindow:
    origin: str
    destination: str
    outbound_start: date
    outbound_end: date
    return_start: date
    return_end: date

    def combo_count(self) -> int:
        ob = (self.outbound_end - self.outbound_start).days + 1
        rt = (self.return_end - self.return_start).days + 1
        return max(0, ob) * max(0, rt)


@dataclass
class SweepResult:
    windows_planned: int
    calls_made: int               # SearchAPI calls only (legacy field name)
    entries_stored: int           # SearchAPI grid rows stored
    curve_calls_made: int = 0     # Sky Scrapper calls used for curve
    curve_entries_stored: int = 0 # Sky Scrapper curve rows stored


GOOGLE_HORIZON_DAYS = 330
COMBO_CAP = 200


def outbound_width_for_span(span: int, *, cap: int = COMBO_CAP) -> int:
    """Largest outbound width W_o with W_o*(W_o + span - 1) <= cap.

    Solves the quadratic W_o^2 + (span-1)*W_o - cap <= 0. Returns at
    least 1. When span alone exceeds the cap (W_o would be 0), returns
    1 and the caller must chunk the stay range instead.
    """
    a = span - 1
    w = math.floor((-a + math.sqrt(a * a + 4 * cap)) / 2)
    return max(1, w)


def _stay_chunks(min_stay: int, max_stay: int, *, cap: int = COMBO_CAP) -> list[tuple[int, int]]:
    """Split [min_stay, max_stay] into sub-ranges each with span <= cap.

    Only needed in the degenerate case span > cap (a >300-day-wide stay
    range). With W_o pinned at 1, each rectangle covers one departure day
    and up to `cap` stay values, so we chunk the stay axis.
    """
    span = max_stay - min_stay + 1
    if span <= cap:
        return [(min_stay, max_stay)]
    chunks: list[tuple[int, int]] = []
    lo = min_stay
    while lo <= max_stay:
        hi = min(lo + cap - 1, max_stay)
        chunks.append((lo, hi))
        lo = hi + 1
    return chunks


def plan_windows(
    route: RouteConfig, *, today: date | None = None,
) -> tuple[list[SweepWindow], list[str]]:
    """Plan the calendar rectangles covering the whole search window.

    Returns (windows, notes). Each rectangle covers every stay length in
    [min_stay, max_stay] for the departure days it spans, stays under the
    200-combo cap, and tiles the departure axis with no overlap. Windows
    are clamped to `today` (no past departures) and Google's ~330-day
    booking horizon; each clamp adds a note.
    """
    today = today or date.today()
    sw = route.search_window
    min_stay = route.stay.min_days
    max_stay = route.stay.max_days

    notes: list[str] = []

    # --- horizon + past clamps on the departure axis ---
    earliest_dep = sw.earliest_departure
    if earliest_dep < today:
        skipped = (today - earliest_dep).days
        earliest_dep = today
        notes.append(
            f"{skipped} days before today excluded (can't book the past)"
        )
    horizon = today + timedelta(days=GOOGLE_HORIZON_DAYS)
    # A departure needs at least min_stay days before latest_return
    # (and must respect the optional latest_departure cap).
    latest_dep = sw.last_departure(min_stay)
    if latest_dep > horizon:
        dropped_days = (latest_dep - horizon).days
        latest_dep = horizon
        notes.append(
            f"departures past {horizon.isoformat()} excluded "
            f"(~{dropped_days}d beyond Google's {GOOGLE_HORIZON_DAYS}-day horizon)"
        )

    out: list[SweepWindow] = []
    if latest_dep < earliest_dep:
        notes.append("no departures in range after clamps — nothing to sweep")
        return out, notes

    chunks = _stay_chunks(min_stay, max_stay)
    for s1, s2 in chunks:
        span = s2 - s1 + 1
        w_o = outbound_width_for_span(span)
        for origin in route.origins:
            for destination in route.destinations:
                ob_start = earliest_dep
                while ob_start <= latest_dep:
                    ob_end = min(ob_start + timedelta(days=w_o - 1), latest_dep)
                    return_start = ob_start + timedelta(days=s1)
                    return_end = min(ob_end + timedelta(days=s2), sw.latest_return)
                    # Clamp return_end to horizon+max reasonable — a return
                    # can legitimately be past the horizon since we only
                    # book the outbound within horizon; leave as-is.
                    if return_end >= return_start:
                        out.append(SweepWindow(
                            origin=origin,
                            destination=destination,
                            outbound_start=ob_start,
                            outbound_end=ob_end,
                            return_start=return_start,
                            return_end=return_end,
                        ))
                    ob_start = ob_end + timedelta(days=1)
    return out, notes


def run_sweep(
    *,
    conn,
    client: SearchApiClient | None,
    route: RouteConfig,
    max_calls: int | None = None,
    dry_run: bool = False,
    today: date | None = None,
    skyscanner_client: SkyScrapperClient | None = None,
    skyscanner_planned: bool = False,
    windows: list[SweepWindow] | None = None,
) -> SweepResult:
    """Run the full sweep: Sky Scrapper curve + SearchAPI grid.

    Sky Scrapper runs first (cheap: 1 call per origin-destination pair).
    SearchAPI runs second across the planned windows.

    `windows`: when None (CLI path), plan them here and apply smart-skip
    per window. When provided (RunPlan path), execute EXACTLY that list —
    no re-planning, no skip re-evaluation, since the planner already made
    those decisions. This guarantees quote == execution.

    `skyscanner_planned` controls the dry-run preview when no real client
    is available: pass True to log the planned Sky Scrapper calls.
    """
    today = today or date.today()
    precomputed = windows is not None
    if precomputed:
        window_list = list(windows)
    else:
        window_list, plan_notes = plan_windows(route, today=today)
        for n in plan_notes:
            LOG.info("sweep plan note: %s", n)
    windows = window_list
    LOG.info("sweep route=%s windows=%d precomputed=%s",
             route.name, len(windows), precomputed)

    # ----- Sky Scrapper curve pass -----
    curve_calls = 0
    curve_stored = 0
    snapshot_at = _now_iso()
    sky_active = skyscanner_client is not None or skyscanner_planned
    if skyscanner_client is not None and not dry_run:
        curve_calls, curve_stored = _run_skyscanner_curve(
            conn=conn,
            client=skyscanner_client,
            route=route,
            snapshot_at=snapshot_at,
            today=today,
        )
    elif sky_active and dry_run:
        for origin in route.origins:
            for destination in route.destinations:
                LOG.info("plan skyscanner curve %s->%s fromDate=%s",
                         origin, destination, max(today, route.search_window.earliest_departure))

    if dry_run:
        for w in windows:
            if precomputed:
                skip, reason = False, ""
            else:
                skip, reason = _should_skip_window(conn, route, w, today)
            tag = f" SKIP({reason})" if skip else ""
            LOG.info(
                "plan origin=%s dst=%s ob=%s..%s ret=%s..%s combos=%d%s",
                w.origin, w.destination,
                w.outbound_start, w.outbound_end,
                w.return_start, w.return_end, w.combo_count(), tag,
            )
        return SweepResult(
            windows_planned=len(windows),
            calls_made=0,
            entries_stored=0,
            curve_calls_made=0,
            curve_entries_stored=0,
        )

    if client is None:
        LOG.info("no SearchAPI client; skipping grid pass")
        return SweepResult(
            windows_planned=len(windows),
            calls_made=0,
            entries_stored=0,
            curve_calls_made=curve_calls,
            curve_entries_stored=curve_stored,
        )

    calls = 0
    stored = 0
    skipped = 0
    for w in windows:
        if max_calls is not None and calls >= max_calls:
            LOG.info("sweep stopping at max_calls=%d", max_calls)
            break
        # When windows are precomputed by the planner, smart-skip was
        # already applied there — don't re-evaluate (that would drift
        # execution from the quote).
        if not precomputed:
            skip, reason = _should_skip_window(conn, route, w, today)
            if skip:
                skipped += 1
                LOG.info(
                    "sweep skip origin=%s dst=%s ob=%s..%s reason=%s",
                    w.origin, w.destination, w.outbound_start, w.outbound_end, reason,
                )
                continue
        try:
            resp = client.calendar(
                origin=w.origin,
                destination=w.destination,
                outbound_start=w.outbound_start,
                outbound_end=w.outbound_end,
                return_start=w.return_start,
                return_end=w.return_end,
                currency=route.currency,
            )
        except SearchApiError as exc:
            LOG.error(
                "sweep call failed origin=%s dst=%s ob=%s..%s err=%s",
                w.origin, w.destination, w.outbound_start, w.outbound_end, exc,
            )
            calls += 1
            continue
        calls += 1
        rows = list(_entries_to_rows(
            entries=resp.entries,
            window=w,
            route=route,
            snapshot_at=snapshot_at,
        ))
        stored += insert_calendar_rows(conn, rows)
        LOG.info(
            "sweep stored origin=%s dst=%s ob=%s..%s entries=%d",
            w.origin, w.destination, w.outbound_start, w.outbound_end, len(rows),
        )
    LOG.info(
        "sweep done route=%s searchapi_calls=%d skipped=%d stored=%d "
        "skyscanner_calls=%d curve_rows=%d",
        route.name, calls, skipped, stored, curve_calls, curve_stored,
    )
    return SweepResult(
        windows_planned=len(windows),
        calls_made=calls,
        entries_stored=stored,
        curve_calls_made=curve_calls,
        curve_entries_stored=curve_stored,
    )


def _run_skyscanner_curve(
    *,
    conn,
    client: SkyScrapperClient,
    route: RouteConfig,
    snapshot_at: str,
    today: date,
) -> tuple[int, int]:
    """Per origin/destination pair: one Sky Scrapper getPriceCalendar call.

    Each call resolves airport IDs (cached in DB after first sighting),
    fetches up to ~206 days of departure-date prices, and persists them
    into `departure_curves`.

    Returns (calls_made, rows_stored). Airport-lookup calls count too.
    """
    calls = 0
    stored = 0
    # Sky Scrapper rejects from_date in the past. Clamp to today.
    earliest = route.search_window.earliest_departure
    from_date = max(today, earliest)

    for origin in route.origins:
        for destination in route.destinations:
            # Track lookups by checking the cache state before/after.
            from . import db as db_mod
            lookups_needed = 0
            if db_mod.lookup_airport(conn, origin) is None:
                lookups_needed += 1
            if db_mod.lookup_airport(conn, destination) is None:
                lookups_needed += 1

            try:
                resp = client.calendar_curve(
                    origin=origin,
                    destination=destination,
                    from_date=from_date,
                    currency=route.currency,
                )
            except SkyScrapperError as exc:
                LOG.error(
                    "skyscanner curve failed %s->%s err=%s", origin, destination, exc,
                )
                calls += 1 + lookups_needed
                continue
            calls += 1 + lookups_needed

            rows = [
                CurveRow(
                    snapshot_at=snapshot_at,
                    route_id=route.name,
                    source=SKYSCANNER_SOURCE,
                    origin=origin,
                    destination=destination,
                    departure_date=e.departure_date,
                    price=e.price,
                    price_group=e.price_group,
                    currency=route.currency,
                )
                for e in resp.entries
            ]
            stored += insert_curve_rows(conn, rows)
            LOG.info(
                "skyscanner curve %s->%s entries=%d",
                origin, destination, len(rows),
            )
    return calls, stored


def _should_skip_window(
    conn, route: RouteConfig, w: SweepWindow, today: date,
) -> tuple[bool, str]:
    """Decide whether a window can be skipped based on its history.

    Skip only when ALL of:
      * `sweep.skip_if_min_above` and `sweep.skip_grace_days` are set
      * we have at least one prior snapshot inside this window
      * that prior snapshot's minimum price was strictly above
        `skip_if_min_above`
      * the window's earliest outbound is more than `skip_grace_days`
        days in the future from `today`

    The grace period exists because prices typically drop in the final
    weeks before departure — we don't want to keep ignoring a window
    that may finally be turning cheap.
    """
    threshold = route.sweep.skip_if_min_above
    grace = route.sweep.skip_grace_days
    if threshold is None or grace is None:
        return False, ""
    days_to_departure = (w.outbound_start - today).days
    if days_to_departure <= grace:
        return False, ""
    min_price = _last_snapshot_min_price(conn, route.name, w)
    if min_price is None:
        return False, ""  # never scanned -> always scan
    if min_price > threshold:
        return True, f"prev_min={min_price}>{threshold}"
    return False, ""


def _last_snapshot_min_price(conn, route_id: str, w: SweepWindow) -> int | None:
    """Return min(price) from the most recent prior snapshot of this window.

    Returns None if no prior snapshot exists.
    """
    row = conn.execute(
        """
        SELECT MIN(cs.price) AS min_price
        FROM calendar_snapshots cs
        WHERE cs.route_id = ?
          AND cs.origin = ?
          AND cs.destination = ?
          AND cs.departure_date BETWEEN ? AND ?
          AND cs.return_date BETWEEN ? AND ?
          AND cs.snapshot_at = (
              SELECT MAX(snapshot_at) FROM calendar_snapshots
              WHERE route_id = ?
                AND origin = ?
                AND destination = ?
                AND departure_date BETWEEN ? AND ?
                AND return_date BETWEEN ? AND ?
          )
        """,
        (
            route_id, w.origin, w.destination,
            w.outbound_start.isoformat(), w.outbound_end.isoformat(),
            w.return_start.isoformat(), w.return_end.isoformat(),
            route_id, w.origin, w.destination,
            w.outbound_start.isoformat(), w.outbound_end.isoformat(),
            w.return_start.isoformat(), w.return_end.isoformat(),
        ),
    ).fetchone()
    if not row:
        return None
    val = row["min_price"]
    return int(val) if val is not None else None


def _entries_to_rows(
    *,
    entries: Iterable[CalendarEntry],
    window: SweepWindow,
    route: RouteConfig,
    snapshot_at: str,
) -> Iterator[CalendarRow]:
    for e in entries:
        if e.has_no_flights:
            continue
        try:
            dep = date.fromisoformat(e.departure_date)
            ret = date.fromisoformat(e.return_date)
        except ValueError:
            continue
        stay = (ret - dep).days
        yield CalendarRow(
            snapshot_at=snapshot_at,
            route_id=route.name,
            source=SEARCHAPI_SOURCE,
            origin=window.origin,
            destination=window.destination,
            departure_date=e.departure_date,
            return_date=e.return_date,
            stay_days=stay,
            price=e.price,
            currency=route.currency,
            is_lowest_price=e.is_lowest_price,
        )


# ---------------------------------------------------------------------------
# SerpApi calendar STRIP sweep (2026-11-14)
# ---------------------------------------------------------------------------
#
# Geometry. SerpApi's google_flights_calendar engine takes one trip length
# (`round_trip_days`) and a departure range, and prices every departure
# day of the range. So the (departure x stay) rectangle is priced one
# STAY LENGTH at a time: a "strip" = (origin, destination, stay, departure
# range). The NBO window (65 departure days x stays 60-90) is 31 strips
# per origin; 62 calls price all 4,030 date pairs — vs 5 pairs per scan
# from the point-query grid.
#
# State. Each call is logged in calendar_sweeps (what was asked, how it
# landed). A strip is DUE when no ok/empty call in the last
# SERPAPI_SWEEP_REFRESH_DAYS covers its whole departure range. That one
# rule gives resume-after-partial-sweep, no re-buying of empty strips,
# and "a moved window is unswept" for free.

# Re-price each strip every two weeks. The old full sweeps (Jul-Aug)
# moved little in 15 days (price correlation 0.90-0.98, median change
# 4-7 EUR, the minimum <= 2 EUR), and 2 sweeps/month (~124 searches)
# plus the regular scans (~91) fit the free 250 minus its 25 margin.
SERPAPI_SWEEP_REFRESH_DAYS = 14

# Departure days per call. Probed 2026-11-14: one MAD->NBO call with a
# 65-day range (27 Dec - 1 Mar) and a 90-day stay returned all 65 days,
# 1 credit, 4.6 s. Longer ranges are unproven, so wider windows are
# chunked at the proven size rather than risk a silently truncated strip.
SERPAPI_CALENDAR_MAX_DEPARTURE_DAYS = 65

# Wall-clock budget for one sweep pass. The first real sweep (2026-11-14)
# averaged 9.7 s per call (2.8-21.5 s): 62 strips in 10 minutes. 15 min
# leaves the rest of the batch (scraper verification) inside the CI job
# even on a slow day. Strips not reached stay due for the next run.
STRIP_SWEEP_WALL_BUDGET_S = 900

# Stop after this many failures in a row: a dead key or a provider outage
# should cost a handful of charged calls, not the whole reservation.
STRIP_SWEEP_MAX_CONSECUTIVE_ERRORS = 3


@dataclass(frozen=True)
class CalendarStrip:
    """One google_flights_calendar call: every departure day in
    [dep_start, dep_end], each returning `stay_days` later."""
    origin: str
    destination: str
    stay_days: int
    dep_start: date
    dep_end: date

    @property
    def departures(self) -> int:
        return (self.dep_end - self.dep_start).days + 1


@dataclass
class StripSweepResult:
    planned: int
    calls: int = 0
    cells_stored: int = 0
    empty: int = 0
    errors: int = 0
    stopped_early: str | None = None    # why the pass ended before the plan


def plan_strips(
    route: RouteConfig, *, today: date,
    max_departure_days: int = SERPAPI_CALENDAR_MAX_DEPARTURE_DAYS,
) -> list[CalendarStrip]:
    """Every strip that covers the search window, before freshness.

    Per stay s, departures run from max(earliest_departure, today) to the
    last day whose s-day trip still ends by latest_return (and respects
    latest_departure), clamped so both legs sit inside Google's booking
    horizon. Ranges longer than `max_departure_days` are chunked."""
    sw = route.search_window
    horizon = today + timedelta(days=GOOGLE_HORIZON_DAYS)
    lo = max(sw.earliest_departure, today)
    out: list[CalendarStrip] = []
    for origin in route.origins:
        for destination in route.destinations:
            for stay in range(max(1, route.stay.min_days),
                              route.stay.max_days + 1):
                hi = min(sw.last_departure(stay),
                         horizon - timedelta(days=stay))
                start = lo
                while start <= hi:
                    end = min(start + timedelta(days=max_departure_days - 1),
                              hi)
                    out.append(CalendarStrip(origin, destination, stay,
                                             start, end))
                    start = end + timedelta(days=1)
    return out


def _radical_inverse(i: int) -> float:
    """Base-2 van der Corput: 0, .5, .25, .75, ... — orders stay lengths
    so any PREFIX of the order is spread across the whole stay range."""
    inv, f = 0.0, 0.5
    while i:
        inv += f * (i & 1)
        i >>= 1
        f *= 0.5
    return inv


def _covered(intervals: list[tuple[date, date]], lo: date, hi: date) -> bool:
    """True when the union of [a, b] day intervals contains [lo, hi]."""
    reach = lo - timedelta(days=1)
    for a, b in sorted(intervals):
        if a > reach + timedelta(days=1):
            break
        reach = max(reach, b)
        if reach >= hi:
            return True
    return reach >= hi


def due_strips(
    conn, route: RouteConfig, strips: list[CalendarStrip], *, today: date,
    refresh_days: int = SERPAPI_SWEEP_REFRESH_DAYS,
    source: str = SERPAPI_CALENDAR_SOURCE,
) -> list[CalendarStrip]:
    """The strips that need pricing, in the order to price them.

    A strip is FRESH when calls logged less than `refresh_days` days ago
    cover its whole departure range. An `ok` call counts; an error never
    does. An `empty` call counts only when the call before it for the
    same origin, destination and stay was empty too: Google's "no
    results" is retried once before it is believed. In the first real
    sweep (2026-11-14) one strip of 62 came back empty while the strips
    on either side of it returned all 65 days.

    Order: never-swept strips first (an empty not yet confirmed counts as
    never swept), then the stalest; ties are spread across stay lengths
    (van der Corput) and alternate origins, so a sweep that is cut short
    by budget or wall clock still samples the whole window. One DB query
    for all strips."""
    cutoff = (today - timedelta(days=refresh_days)).isoformat()
    fresh: dict[tuple, list[tuple[date, date]]] = {}
    last: dict[tuple, str] = {}
    previous: dict[tuple, str] = {}       # status of the key's last call
    for r in calendar_sweep_log(conn, route.name, source=source):
        key = (r["origin"], r["destination"], int(r["stay_days"]))
        status, before = r["status"], previous.get(key)
        previous[key] = status
        if not (status == "ok" or (status == "empty" and before == "empty")):
            continue
        try:
            a, b = date.fromisoformat(r["dep_start"]), date.fromisoformat(r["dep_end"])
        except (TypeError, ValueError):
            continue
        last[key] = max(last.get(key, ""), r["swept_at"])
        # Fresh = swept on a day AFTER the cutoff day: with a 14-day
        # refresh, a strip swept on a Saturday is due again two Saturdays
        # later, on the same scan slot.
        if str(r["swept_at"])[:10] > cutoff:
            fresh.setdefault(key, []).append((a, b))
    origins = {o: i for i, o in enumerate(route.origins)}
    dests = {d: i for i, d in enumerate(route.destinations)}
    due = [s for s in strips
           if not _covered(fresh.get((s.origin, s.destination, s.stay_days), []),
                           s.dep_start, s.dep_end)]
    min_stay = route.stay.min_days
    due.sort(key=lambda s: (
        last.get((s.origin, s.destination, s.stay_days), ""),
        _radical_inverse(s.stay_days - min_stay),
        origins.get(s.origin, 99), dests.get(s.destination, 99),
        s.dep_start))
    return due


def _empty_reason(resp) -> str:
    """Why a strip stored no cells, for the log line."""
    raw = getattr(resp, "raw", None)
    if isinstance(raw, dict) and raw.get("error"):
        return str(raw["error"])[:160]
    n = len(resp.entries)
    return (f"{n} cells returned, none inside the window" if n
            else "no prices in the response")


def _cell_in_window(route: RouteConfig, e: CalendarEntry) -> int | None:
    """Stay length of a cell that sits inside the CURRENT search window
    (the same rules alerts and followup apply), else None."""
    sw = route.search_window
    try:
        dep = date.fromisoformat(e.departure_date)
        ret = date.fromisoformat(e.return_date)
    except ValueError:
        return None
    stay = (ret - dep).days
    if not (route.stay.min_days <= stay <= route.stay.max_days):
        return None
    if dep < sw.earliest_departure or dep > sw.last_departure(route.stay.min_days):
        return None
    if ret > sw.latest_return:
        return None
    return stay


def run_strip_sweep(
    *,
    conn,
    client,
    route: RouteConfig,
    strips: Iterable[CalendarStrip],
    wall_budget_s: float = STRIP_SWEEP_WALL_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> StripSweepResult:
    """Price EXACTLY the planned strips (quote == execution), storing the
    in-window cells as `serpapi_calendar` rows and logging every call.

    `client` is the (guarded) SerpApiClient: each calendar() call is
    charged to the ledger before it runs. Stops early on the wall budget
    or after STRIP_SWEEP_MAX_CONSECUTIVE_ERRORS failures in a row; the
    strips it did not reach stay due, so the next run picks them up.
    QuotaExceeded is not caught here: it means the executor outran its own
    reservation (a planner/executor bug), and execute_search records it
    as a failed, degraded search."""
    strips = list(strips)
    res = StripSweepResult(planned=len(strips))
    snapshot_at = _now_iso()
    deadline = clock() + wall_budget_s
    consecutive_errors = 0
    for strip in strips:
        if clock() > deadline:
            res.stopped_early = f"wall budget {wall_budget_s:.0f}s"
            break
        if consecutive_errors >= STRIP_SWEEP_MAX_CONSECUTIVE_ERRORS:
            res.stopped_early = f"{consecutive_errors} errors in a row"
            break
        res.calls += 1
        try:
            resp = client.calendar(
                origin=strip.origin, destination=strip.destination,
                stay_days=strip.stay_days,
                outbound_start=strip.dep_start, outbound_end=strip.dep_end,
                currency=route.currency)
        except SerpApiError as exc:
            res.errors += 1
            consecutive_errors += 1
            LOG.error("calendar strip failed %s->%s stay=%d %s..%s: %s",
                      strip.origin, strip.destination, strip.stay_days,
                      strip.dep_start, strip.dep_end, exc)
            insert_calendar_sweep(
                conn, route_id=route.name, source=SERPAPI_CALENDAR_SOURCE,
                origin=strip.origin, destination=strip.destination,
                stay_days=strip.stay_days, dep_start=strip.dep_start.isoformat(),
                dep_end=strip.dep_end.isoformat(), swept_at=snapshot_at,
                cells=0, status="error")
            continue
        consecutive_errors = 0
        rows: list[CalendarRow] = []
        for e in resp.entries:
            stay = _cell_in_window(route, e)
            if stay is None:
                continue
            rows.append(CalendarRow(
                snapshot_at=snapshot_at, route_id=route.name,
                source=SERPAPI_CALENDAR_SOURCE, origin=strip.origin,
                destination=strip.destination,
                departure_date=e.departure_date, return_date=e.return_date,
                stay_days=stay, price=e.price, currency=route.currency,
                is_lowest_price=e.is_lowest_price))
        res.cells_stored += insert_calendar_rows(conn, rows)
        if not rows:
            res.empty += 1
            LOG.warning("calendar strip %s->%s stay=%d %s..%s came back empty: %s",
                        strip.origin, strip.destination, strip.stay_days,
                        strip.dep_start, strip.dep_end, _empty_reason(resp))
        insert_calendar_sweep(
            conn, route_id=route.name, source=SERPAPI_CALENDAR_SOURCE,
            origin=strip.origin, destination=strip.destination,
            stay_days=strip.stay_days, dep_start=strip.dep_start.isoformat(),
            dep_end=strip.dep_end.isoformat(), swept_at=snapshot_at,
            cells=len(rows), status="ok" if rows else "empty")
        LOG.info("calendar strip %s->%s stay=%d %s..%s cells=%d (of %d returned)",
                 strip.origin, strip.destination, strip.stay_days,
                 strip.dep_start, strip.dep_end, len(rows), len(resp.entries))
    LOG.info("calendar sweep route=%s planned=%d calls=%d cells=%d empty=%d "
             "errors=%d%s", route.name, res.planned, res.calls,
             res.cells_stored, res.empty, res.errors,
             f" stopped_early={res.stopped_early}" if res.stopped_early else "")
    return res


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
