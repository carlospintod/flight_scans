"""lib.db.recent_alerts window clamp — the results page shows only alerts
for trips the current search window still covers (UI clarity pass,
2026-11-13). Offline, in-memory SQLite."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from lib.db import AlertRow, ensure_schema, insert_alert_rows, recent_alerts  # noqa: E402


def _alert(fired: str, dep: str, ret: str, price: int) -> AlertRow:
    return AlertRow(fired, "t", "serpapi", "MAD", "NBO", dep, ret,
                    price, "EUR", 600, 10.0, "new_low")


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    insert_alert_rows(c, [
        # Old window (Nov departures) — newest, but outside the window.
        _alert("2026-09-30T11:00:00Z", "2026-11-12", "2027-01-15", 481),
        # Inside: departs 28 Jan, 82-day stay.
        _alert("2026-09-01T10:00:00Z", "2027-01-28", "2027-04-20", 557),
        # Departs after the departure cap (1 Mar).
        _alert("2026-08-01T10:00:00Z", "2027-03-05", "2027-05-10", 540),
        # Stay too short (30 days).
        _alert("2026-07-01T10:00:00Z", "2027-01-10", "2027-02-09", 500),
        # One-way row never matches a stay bound.
        _alert("2026-06-01T10:00:00Z", "2027-01-10", "", 300),
    ])
    return c


def test_unclamped_returns_everything_newest_first(conn):
    rows = recent_alerts(conn, "t")
    assert [r["price"] for r in rows] == [481, 557, 540, 500, 300]


def test_window_clamp_keeps_only_covered_trips(conn):
    rows = recent_alerts(
        conn, "t",
        earliest_departure="2026-12-27", latest_departure="2027-03-01",
        latest_return="2027-05-30", min_stay=60, max_stay=90,
    )
    assert [r["price"] for r in rows] == [557]


def test_limit_applies_after_the_clamp(conn):
    # The clamp must run in SQL: a LIMIT taken first would return only
    # out-of-window rows and the page would wrongly show "no alerts".
    rows = recent_alerts(
        conn, "t", limit=1,
        earliest_departure="2026-12-27", latest_return="2027-05-30",
        min_stay=60, max_stay=90,
    )
    assert [r["price"] for r in rows] == [557]
