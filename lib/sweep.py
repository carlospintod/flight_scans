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
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Iterator

from .config import RouteConfig
from .db import CalendarRow, CurveRow, insert_calendar_rows, insert_curve_rows
from .searchapi_io import CalendarEntry, SearchApiClient, SearchApiError, SOURCE_ID as SEARCHAPI_SOURCE
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
    # A departure needs at least min_stay days before latest_return.
    latest_dep = sw.latest_return - timedelta(days=min_stay)
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
