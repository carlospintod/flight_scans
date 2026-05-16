"""Tests for the smart-skip logic in lib/sweep.run_sweep.

We don't hit the API here — we exercise `_should_skip_window` directly
against a seeded SQLite DB.
"""

from datetime import date, datetime
from pathlib import Path

from lib.config import (
    AlertParams,
    FollowupParams,
    RouteConfig,
    SearchWindow,
    StayPreferences,
    SweepParams,
)
from lib.db import CalendarRow, connect, ensure_schema, insert_calendar_rows, upsert_route
from lib.sweep import SweepWindow, _should_skip_window


def _route(skip_if_min_above=800, skip_grace_days=60) -> RouteConfig:
    return RouteConfig(
        name="t",
        origins=("MAD",),
        destinations=("NBO",),
        search_window=SearchWindow(
            earliest_departure=date(2026, 9, 1),
            latest_return=date(2027, 5, 31),
        ),
        stay=StayPreferences(min_days=60, max_days=90),
        currency="EUR",
        sweep=SweepParams(
            14, 14, 3, 14,
            skip_if_min_above=skip_if_min_above,
            skip_grace_days=skip_grace_days,
        ),
        followup=FollowupParams(),
        alerts=AlertParams(15, 30, 4),
    )


def _window(ob_start: date) -> SweepWindow:
    return SweepWindow(
        origin="MAD",
        destination="NBO",
        outbound_start=ob_start,
        outbound_end=ob_start.replace(day=min(ob_start.day + 13, 28)),
        return_start=date(ob_start.year, ob_start.month, ob_start.day) if False else
                     date.fromisoformat(ob_start.isoformat()),
        return_end=date.fromisoformat(ob_start.isoformat()),
    )


def _seed(conn, window: SweepWindow, prices: list[int]):
    """Drop N rows for this window's date range into one snapshot."""
    snapshot_at = datetime(2026, 5, 1).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for i, p in enumerate(prices):
        # all rows inside the window's date box (start dates)
        rows.append(CalendarRow(
            snapshot_at=snapshot_at,
            route_id="t",
            source="searchapi",
            origin="MAD",
            destination="NBO",
            departure_date=window.outbound_start.isoformat(),
            return_date=window.return_start.isoformat(),
            stay_days=60,
            price=p,
            currency="EUR",
            is_lowest_price=False,
        ))
    insert_calendar_rows(conn, rows)


def test_skip_when_prior_min_above_threshold_and_far_enough(tmp_path: Path):
    db = tmp_path / "t.db"
    today = date(2026, 5, 16)
    # window outbound starts 2026-09-01 = 108 days from today, > grace=60
    w = SweepWindow(
        origin="MAD", destination="NBO",
        outbound_start=date(2026, 9, 1), outbound_end=date(2026, 9, 14),
        return_start=date(2026, 11, 1), return_end=date(2026, 11, 14),
    )
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        _seed(conn, w, [820, 870, 900])
        skip, reason = _should_skip_window(conn, _route(), w, today)
    assert skip is True
    assert "prev_min=820" in reason


def test_do_not_skip_when_inside_grace(tmp_path: Path):
    db = tmp_path / "t.db"
    today = date(2026, 8, 1)  # within ~30 days of Sep 1
    w = SweepWindow(
        origin="MAD", destination="NBO",
        outbound_start=date(2026, 9, 1), outbound_end=date(2026, 9, 14),
        return_start=date(2026, 11, 1), return_end=date(2026, 11, 14),
    )
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        _seed(conn, w, [820, 870, 900])
        skip, _ = _should_skip_window(conn, _route(), w, today)
    assert skip is False


def test_do_not_skip_when_prior_had_cheap_cell(tmp_path: Path):
    db = tmp_path / "t.db"
    today = date(2026, 5, 16)
    w = SweepWindow(
        origin="MAD", destination="NBO",
        outbound_start=date(2026, 9, 1), outbound_end=date(2026, 9, 14),
        return_start=date(2026, 11, 1), return_end=date(2026, 11, 14),
    )
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        _seed(conn, w, [900, 750, 870])  # 750 is below 800 threshold
        skip, _ = _should_skip_window(conn, _route(), w, today)
    assert skip is False


def test_do_not_skip_when_no_prior_snapshot(tmp_path: Path):
    db = tmp_path / "t.db"
    today = date(2026, 5, 16)
    w = SweepWindow(
        origin="MAD", destination="NBO",
        outbound_start=date(2026, 9, 1), outbound_end=date(2026, 9, 14),
        return_start=date(2026, 11, 1), return_end=date(2026, 11, 14),
    )
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        skip, _ = _should_skip_window(conn, _route(), w, today)
    assert skip is False


def test_do_not_skip_when_thresholds_unset(tmp_path: Path):
    db = tmp_path / "t.db"
    today = date(2026, 5, 16)
    w = SweepWindow(
        origin="MAD", destination="NBO",
        outbound_start=date(2026, 9, 1), outbound_end=date(2026, 9, 14),
        return_start=date(2026, 11, 1), return_end=date(2026, 11, 14),
    )
    route_no_skip = _route(skip_if_min_above=None, skip_grace_days=None)
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route_no_skip)
        _seed(conn, w, [1000, 1100])
        skip, _ = _should_skip_window(conn, route_no_skip, w, today)
    assert skip is False
