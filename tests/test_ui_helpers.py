"""Tests for the read-side helpers in ui/_common.py.

These run against an in-memory SQLite DB seeded with fixture rows. They
catch SQL errors (typos, JOIN mistakes, missing columns) without needing
the live tracker.db or any network.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# These imports must come after sys.path patching.
from lib.config import (  # noqa: E402
    AlertParams, FollowupParams, RouteConfig, SearchWindow,
    StayPreferences, SweepParams,
)
from lib.db import (  # noqa: E402
    AlertRow, CalendarRow, CurveRow, PointRow,
    ensure_schema, insert_alert_rows, insert_calendar_rows,
    insert_curve_rows, insert_point_rows, upsert_route,
)


ROUTE = RouteConfig(
    name="t",
    origins=("MAD", "BCN"),
    destinations=("NBO",),
    search_window=SearchWindow(
        earliest_departure=date(2026, 9, 1),
        latest_return=date(2027, 5, 31),
    ),
    stay=StayPreferences(min_days=30, max_days=90),
    currency="EUR",
    sweep=SweepParams(14, 14, 3, 14),
    followup=FollowupParams(),
    alerts=AlertParams(15, 30, 4),
)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def conn(tmp_path):
    """Seeded in-memory DB connection ready for read-side tests."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    upsert_route(c, ROUTE)

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=14)

    # Two SearchAPI sweeps, same itinerary, price dropped.
    insert_calendar_rows(c, [
        CalendarRow(_iso(old), "t", "searchapi", "MAD", "NBO",
                    "2026-10-05", "2026-12-05", 61, 720, "EUR", False),
        CalendarRow(_iso(now), "t", "searchapi", "MAD", "NBO",
                    "2026-10-05", "2026-12-05", 61, 540, "EUR", True),
        # A second itinerary, only the most-recent snap matters
        CalendarRow(_iso(now), "t", "searchapi", "BCN", "NBO",
                    "2026-10-10", "2026-12-15", 66, 612, "EUR", False),
        # Outside the stay range — should not surface
        CalendarRow(_iso(now), "t", "searchapi", "MAD", "NBO",
                    "2026-10-05", "2026-10-15", 10, 320, "EUR", False),
    ])
    insert_point_rows(c, [
        PointRow(_iso(now), "t", "searchapi", "MAD", "NBO",
                 "2026-10-05", "2026-12-05", 0, 540, "EUR",
                 "Kenya Airways", 720, 1, False),
        PointRow(_iso(now), "t", "skyscanner", "MAD", "NBO",
                 "2026-10-05", "2026-12-05", 0, 555, "EUR",
                 "KLM + Kenya Airways", 740, 1, True),
        PointRow(_iso(now), "t", "searchapi", "BCN", "NBO",
                 "2026-10-10", "2026-12-15", 0, 612, "EUR",
                 "Etihad Airways", 780, 1, False),
    ])
    insert_curve_rows(c, [
        CurveRow(_iso(now), "t", "skyscanner", "MAD", "NBO",
                 "2026-10-05", 489.5, "low", "EUR"),
        CurveRow(_iso(now), "t", "skyscanner", "BCN", "NBO",
                 "2026-10-10", 612.0, "medium", "EUR"),
    ])
    insert_alert_rows(c, [
        AlertRow(_iso(now), "t", "searchapi", "MAD", "NBO",
                 "2026-10-05", "2026-12-05", 540, "EUR", 720, 25.0),
    ])
    return c


# Import the helpers under test after ROUTE is defined to keep this module
# self-contained.
from ui._common import (  # noqa: E402
    alerts_dataframe,
    carrier_mix,
    latest_grid_for_heatmap,
    next_action_hint,
    recent_alert_count,
    recent_capture_summary,
    stops_distribution,
    top_alternatives,
)


def test_recent_capture_summary_counts_recent_rows(conn):
    summary = recent_capture_summary(conn, ROUTE)
    assert summary["calendar"] >= 3  # the 3 "now" rows
    assert summary["curve"] == 2
    assert summary["point"] == 3


def test_recent_alert_count(conn):
    assert recent_alert_count(conn, ROUTE, days=7) == 1
    assert recent_alert_count(conn, ROUTE, days=1) == 1


def test_next_action_hint_runs(conn):
    """Smoke test — just ensure it returns a non-empty string."""
    hint = next_action_hint(conn, ROUTE)
    assert isinstance(hint, str)
    assert hint


def test_latest_grid_for_heatmap_returns_in_range(conn):
    df = latest_grid_for_heatmap(
        conn, ROUTE, origin="MAD", source="searchapi",
        min_stay=30, max_stay=90,
    )
    assert not df.empty
    # The MAD-NBO 10/05-12/05 itinerary (most recent price 540) should be present.
    assert (df["departure_date"] == "2026-10-05").any()
    # The 10-day-stay row should be filtered out.
    assert (df["stay_days"] >= 30).all()


def test_top_alternatives_orders_by_price_and_joins_carriers(conn):
    df = top_alternatives(
        conn, ROUTE, source="searchapi", min_stay=30, max_stay=90, limit=10,
    )
    assert not df.empty
    # Cheapest first.
    prices = df["price"].tolist()
    assert prices == sorted(prices)
    # MAD row should have a carrier joined from point_queries.
    mad_rows = df[(df["origin"] == "MAD") & (df["departure_date"] == "2026-10-05")]
    assert not mad_rows.empty
    assert mad_rows.iloc[0]["top_carrier"] == "Kenya Airways"
    assert int(mad_rows.iloc[0]["stops"]) == 1


def test_top_alternatives_filters_by_origin(conn):
    df = top_alternatives(
        conn, ROUTE, source="searchapi", min_stay=30, max_stay=90,
        origin="BCN", limit=10,
    )
    assert (df["origin"] == "BCN").all()


def test_top_alternatives_skyscanner_uses_curve(conn):
    df = top_alternatives(
        conn, ROUTE, source="skyscanner", min_stay=30, max_stay=90, limit=10,
    )
    assert not df.empty
    assert (df["source"] == "skyscanner").all()
    # No return_date for curve rows.
    assert df["return_date"].isna().all()


def test_carrier_mix(conn):
    df = carrier_mix(conn, ROUTE, source="searchapi", min_stay=30, max_stay=90)
    assert not df.empty
    carriers = set(df["carriers"].tolist())
    assert "Kenya Airways" in carriers
    assert "Etihad Airways" in carriers


def test_stops_distribution(conn):
    df = stops_distribution(conn, ROUTE, source=None, min_stay=30, max_stay=90)
    assert not df.empty
    # We seeded 3 point queries, all with stops=1 — should aggregate.
    assert df["n"].sum() == 3


def test_alerts_dataframe(conn):
    df = alerts_dataframe(conn, ROUTE, limit=10)
    assert not df.empty
    assert df.iloc[0]["origin"] == "MAD"
    assert df.iloc[0]["drop %"] == 25.0
