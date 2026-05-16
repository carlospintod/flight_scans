"""Tier 1: sliding-window calendar sweeps.

Plans a set of (outbound_window x return_window) rectangles that together
cover the configured search window, with overlap between adjacent windows
so we don't miss prices that sit on a boundary.

Key design decisions (see CLAUDE.md):

* The calendar API caps at 200 combos/call; window planner validates
  the rectangle size against that cap.
* Stay length is NOT applied here. We let the calendar engine return
  whatever combinations exist inside the rectangle; analysis layer filters.
* Outbound and return windows slide in lockstep with the same cadence,
  but the return window stays aligned with the outbound window via the
  stay range: return_window starts at outbound_start + min_stay (clipped
  to the search window) and continues for `return_window_days`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Iterator

from .api import CalendarEntry, SearchApiClient, SearchApiError
from .config import RouteConfig
from .db import CalendarRow, insert_calendar_rows

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
    calls_made: int
    entries_stored: int


def plan_windows(route: RouteConfig) -> list[SweepWindow]:
    """Generate the sliding rectangles to call for one sweep run.

    For each (origin, destination) pair we step the outbound start by
    `outbound_window_days - overlap_days` until the window passes the
    latest possible return. The return window for a given outbound is
    anchored at `outbound_start + min_stay` and extends for
    `return_window_days`, clipped to the search window.
    """
    sw = route.search_window
    sweep = route.sweep
    min_stay = route.stay.min_days

    step = sweep.outbound_window_days - sweep.overlap_days
    if step <= 0:
        raise ValueError("sweep.outbound_window_days must exceed sweep.overlap_days")

    out: list[SweepWindow] = []
    for origin in route.origins:
        for destination in route.destinations:
            outbound_start = sw.earliest_departure
            # Stop once the entire outbound rectangle is past the latest
            # plausible departure (latest_return - min_stay).
            latest_outbound_start = sw.latest_return - timedelta(days=min_stay)
            while outbound_start <= latest_outbound_start:
                outbound_end = min(
                    outbound_start + timedelta(days=sweep.outbound_window_days - 1),
                    latest_outbound_start,
                )
                return_start = max(
                    sw.earliest_departure + timedelta(days=min_stay),
                    outbound_start + timedelta(days=min_stay),
                )
                return_end = min(
                    return_start + timedelta(days=sweep.return_window_days - 1),
                    sw.latest_return,
                )
                if return_end < return_start or outbound_end < outbound_start:
                    break
                out.append(SweepWindow(
                    origin=origin,
                    destination=destination,
                    outbound_start=outbound_start,
                    outbound_end=outbound_end,
                    return_start=return_start,
                    return_end=return_end,
                ))
                outbound_start = outbound_start + timedelta(days=step)
    return out


def run_sweep(
    *,
    conn,
    client: SearchApiClient,
    route: RouteConfig,
    max_calls: int | None = None,
    dry_run: bool = False,
    today: date | None = None,
) -> SweepResult:
    today = today or date.today()
    windows = plan_windows(route)
    LOG.info("sweep route=%s windows=%d", route.name, len(windows))

    if dry_run:
        for w in windows:
            skip, reason = _should_skip_window(conn, route, w, today)
            tag = f" SKIP({reason})" if skip else ""
            LOG.info(
                "plan origin=%s dst=%s ob=%s..%s ret=%s..%s combos=%d%s",
                w.origin, w.destination,
                w.outbound_start, w.outbound_end,
                w.return_start, w.return_end, w.combo_count(), tag,
            )
        return SweepResult(windows_planned=len(windows), calls_made=0, entries_stored=0)

    calls = 0
    stored = 0
    skipped = 0
    snapshot_at = _now_iso()
    for w in windows:
        if max_calls is not None and calls >= max_calls:
            LOG.info("sweep stopping at max_calls=%d", max_calls)
            break
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
        "sweep done route=%s calls=%d skipped=%d stored=%d",
        route.name, calls, skipped, stored,
    )
    return SweepResult(windows_planned=len(windows), calls_made=calls, entries_stored=stored)


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
