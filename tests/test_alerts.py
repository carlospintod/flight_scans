"""End-to-end test of the alert evaluator against an in-memory SQLite DB."""

from datetime import date, datetime, timedelta
from pathlib import Path

from lib.alerts import evaluate
from lib.config import (
    AlertParams,
    FollowupParams,
    RouteConfig,
    SearchWindow,
    StayPreferences,
    SweepParams,
)
from lib.db import CalendarRow, connect, ensure_schema, insert_calendar_rows, upsert_route


ROUTE = RouteConfig(
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
    alerts=AlertParams(drop_threshold_pct=15, baseline_window_days=30, min_observations=4),
)

DEP = "2026-09-05"
RET = "2026-10-08"  # 33-day stay → within [30, 60]


def _row(price: int, snapshot_at: datetime, stay_days: int = 33) -> CalendarRow:
    return CalendarRow(
        snapshot_at=snapshot_at.replace(microsecond=0).isoformat() + "Z",
        route_id=ROUTE.name,
        source="searchapi",
        origin="MAD",
        destination="NBO",
        departure_date=DEP,
        return_date=RET,
        stay_days=stay_days,
        price=price,
        currency="EUR",
        is_lowest_price=False,
    )


def test_alert_fires_when_drop_exceeds_threshold(tmp_path: Path):
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"

    today = date(2026, 6, 15)
    rows = [
        _row(600, datetime(2026, 5, 25)),
        _row(610, datetime(2026, 5, 30)),
        _row(590, datetime(2026, 6, 5)),
        _row(595, datetime(2026, 6, 10)),
        # current snapshot ~22% below median 597.5 — should fire.
        _row(465, datetime(2026, 6, 14)),
    ]
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, rows)
        fired = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)

    assert len(fired) == 1
    a = fired[0]
    assert a.origin == "MAD" and a.destination == "NBO"
    assert a.departure_date == DEP and a.return_date == RET
    assert a.price == 465
    assert a.drop_pct >= 15
    assert log_path.exists()
    assert "MAD->NBO" in log_path.read_text(encoding="utf-8")


def test_no_alert_below_min_observations(tmp_path: Path):
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"

    today = date(2026, 6, 15)
    # Only 3 priors -> below min_observations=4.
    rows = [
        _row(600, datetime(2026, 5, 25)),
        _row(610, datetime(2026, 5, 30)),
        _row(595, datetime(2026, 6, 10)),
        _row(400, datetime(2026, 6, 14)),  # huge drop, but ignored
    ]
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, rows)
        fired = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)

    assert fired == []
    assert not log_path.exists()


def test_no_alert_outside_stay_range(tmp_path: Path):
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"

    today = date(2026, 6, 15)
    rows = [
        _row(600, datetime(2026, 5, 25), stay_days=20),
        _row(610, datetime(2026, 5, 30), stay_days=20),
        _row(595, datetime(2026, 6, 5),  stay_days=20),
        _row(605, datetime(2026, 6, 10), stay_days=20),
        _row(450, datetime(2026, 6, 14), stay_days=20),
    ]
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, rows)
        fired = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)

    assert fired == []


def test_baseline_window_excludes_old_observations(tmp_path: Path):
    """Priors outside the baseline_window_days are ignored.

    With baseline_window_days=30 and today=2026-06-15, the window starts
    2026-05-16. Priors from 2026-04-01 should be filtered out.
    """
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"

    today = date(2026, 6, 15)
    rows = [
        _row(900, datetime(2026, 4, 1)),   # outside window — ignored
        _row(910, datetime(2026, 4, 5)),   # outside window — ignored
        _row(905, datetime(2026, 4, 10)),  # outside window — ignored
        _row(920, datetime(2026, 4, 15)),  # outside window — ignored
        # Only 1 prior inside window -> below min_obs.
        _row(600, datetime(2026, 5, 20)),
        _row(465, datetime(2026, 6, 14)),  # current
    ]
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, rows)
        fired = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)

    assert fired == []
