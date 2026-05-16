"""Smoke test the DB layer: schema creation, insert, basic read."""

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
from lib.db import (
    CalendarRow,
    cheapest_recent_itineraries,
    connect,
    ensure_schema,
    insert_calendar_rows,
    upsert_route,
)


def _route() -> RouteConfig:
    return RouteConfig(
        name="t",
        origins=("MAD",),
        destinations=("NBO",),
        search_window=SearchWindow(
            earliest_departure=date(2026, 6, 1),
            latest_return=date(2027, 5, 31),
        ),
        stay=StayPreferences(min_days=30, max_days=60),
        currency="EUR",
        sweep=SweepParams(14, 14, 3, 14),
        followup=FollowupParams(),
        alerts=AlertParams(15, 30, 4),
    )


def _row(snapshot: datetime, dep: str, ret: str, stay: int, price: int) -> CalendarRow:
    return CalendarRow(
        snapshot_at=snapshot.replace(microsecond=0).isoformat() + "Z",
        route_id="t",
        origin="MAD",
        destination="NBO",
        departure_date=dep,
        return_date=ret,
        stay_days=stay,
        price=price,
        currency="EUR",
        is_lowest_price=False,
    )


def test_schema_and_insert_roundtrip(tmp_path: Path):
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        n = insert_calendar_rows(conn, [
            _row(datetime(2026, 6, 1), "2026-09-01", "2026-10-04", 33, 600),
            _row(datetime(2026, 6, 5), "2026-09-01", "2026-10-04", 33, 540),
        ])
    assert n == 2


def test_cheapest_recent_filters_stay_range(tmp_path: Path):
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, _route())
        insert_calendar_rows(conn, [
            # Within stay range: kept.
            _row(datetime(2026, 6, 1), "2026-09-01", "2026-10-04", 33, 700),
            _row(datetime(2026, 6, 5), "2026-09-01", "2026-10-04", 33, 540),
            # Outside stay range: filtered.
            _row(datetime(2026, 6, 5), "2026-09-01", "2026-09-15", 14, 350),
            # Different itinerary, within range, cheaper.
            _row(datetime(2026, 6, 5), "2026-09-10", "2026-10-15", 35, 480),
        ])
        rows = cheapest_recent_itineraries(
            conn, "t", min_stay=30, max_stay=60, limit=10,
        )
    prices = [r["price"] for r in rows]
    # Should include the two most-recent itineraries within range, cheapest first.
    assert prices[0] == 480
    assert 540 in prices
    assert 350 not in prices  # filtered by stay range
    assert 700 not in prices  # superseded by newer 540 snapshot on same itinerary
