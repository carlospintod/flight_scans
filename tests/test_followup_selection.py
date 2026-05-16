"""Test the followup candidate-selection logic.

Both modes are exercised: price-threshold (the new default for this
route) and the legacy baseline-trigger fallback.
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
from lib.followup import select_candidates


def _route(*, watch=600, drop=800, min_stay=60, max_stay=90) -> RouteConfig:
    return RouteConfig(
        name="t",
        origins=("MAD",),
        destinations=("NBO",),
        search_window=SearchWindow(
            earliest_departure=date(2026, 9, 1),
            latest_return=date(2027, 5, 31),
        ),
        stay=StayPreferences(min_days=min_stay, max_days=max_stay),
        currency="EUR",
        sweep=SweepParams(14, 14, 3, 14),
        followup=FollowupParams(watch_below_price=watch, drop_above_price=drop),
        alerts=AlertParams(15, 30, 4),
    )


def _row(snapshot: datetime, dep: str, ret: str, stay: int, price: int) -> CalendarRow:
    return CalendarRow(
        snapshot_at=snapshot.strftime("%Y-%m-%dT%H:%M:%SZ"),
        route_id="t",
        source="searchapi",
        origin="MAD",
        destination="NBO",
        departure_date=dep,
        return_date=ret,
        stay_days=stay,
        price=price,
        currency="EUR",
        is_lowest_price=False,
    )


def test_price_mode_picks_itineraries_seen_cheap_and_still_affordable(tmp_path: Path):
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        insert_calendar_rows(conn, [
            # Itinerary A: was once 580 (under 600), now 720 (under 800). KEEP.
            _row(datetime(2026, 5, 1), "2026-09-05", "2026-11-08", 64, 580),
            _row(datetime(2026, 5, 15), "2026-09-05", "2026-11-08", 64, 720),
            # Itinerary B: only ever expensive. DROP (never below 600).
            _row(datetime(2026, 5, 1), "2026-09-10", "2026-11-15", 66, 900),
            _row(datetime(2026, 5, 15), "2026-09-10", "2026-11-15", 66, 850),
            # Itinerary C: was cheap, but now blown out (>800). DROP.
            _row(datetime(2026, 5, 1), "2026-09-12", "2026-11-20", 69, 540),
            _row(datetime(2026, 5, 15), "2026-09-12", "2026-11-20", 69, 870),
            # Itinerary D: outside stay range. DROP.
            _row(datetime(2026, 5, 1), "2026-09-05", "2026-09-30", 25, 450),
        ])
        candidates = select_candidates(conn, _route(), today=date(2026, 5, 16))

    keys = {(c["departure_date"], c["return_date"]) for c in candidates}
    assert ("2026-09-05", "2026-11-08") in keys
    assert ("2026-09-10", "2026-11-15") not in keys
    assert ("2026-09-12", "2026-11-20") not in keys
    assert ("2026-09-05", "2026-09-30") not in keys
    # Only A qualifies.
    assert len(candidates) == 1
    a = candidates[0]
    assert a["trigger"] == "price_threshold"
    assert a["snapshot_price"] == 720
    assert a["all_time_min"] == 580


def test_price_mode_handles_single_observation(tmp_path: Path):
    """A single cheap observation is enough to put an itinerary on the watch list."""
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        insert_calendar_rows(conn, [
            _row(datetime(2026, 5, 1), "2026-09-05", "2026-11-08", 64, 590),
        ])
        candidates = select_candidates(conn, _route(), today=date(2026, 5, 16))
    assert len(candidates) == 1


def test_legacy_mode_when_thresholds_unset(tmp_path: Path):
    db = tmp_path / "t.db"
    route = _route(watch=None, drop=None)
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [
            # Itinerary flagged is_lowest_price in latest snapshot.
            CalendarRow(
                snapshot_at="2026-05-01T00:00:00Z",
                route_id="t", source="searchapi", origin="MAD", destination="NBO",
                departure_date="2026-09-05", return_date="2026-11-08",
                stay_days=64, price=720, currency="EUR",
                is_lowest_price=True,
            ),
        ])
        candidates = select_candidates(conn, route, today=date(2026, 5, 16))
    assert len(candidates) == 1
    assert candidates[0]["trigger"] == "baseline"
    assert candidates[0]["is_lowest_price"] is True
