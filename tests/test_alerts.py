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


def test_new_low_fires_with_only_two_observations(tmp_path: Path):
    """The new_low alert needs just 1 prior snapshot — fires from the
    second scan, unlike the median-drop rule (>=4 obs). Critical for a
    near-in booking window."""
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"
    today = date(2026, 6, 15)
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, [
            _row(600, datetime(2026, 6, 10)),
            _row(540, datetime(2026, 6, 14)),  # below prev min 600 -> new_low
        ])
        fired = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)
    assert len(fired) == 1
    a = fired[0]
    assert a.alert_type == "new_low"
    assert a.price == 540
    assert a.baseline_median == 600  # reference = previous all-time min
    assert a.drop_pct == 10.0


def test_new_low_respects_watch_price_bar(tmp_path: Path):
    """When followup.watch_below_price is set, a new low ABOVE the bar
    stays silent — a 900->880 twitch isn't actionable signal.

    Regression for the 403-alert flood of 2026-07-05."""
    from dataclasses import replace
    from lib.config import FollowupParams
    route = replace(ROUTE, followup=FollowupParams(
        watch_below_price=650, drop_above_price=800))
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"
    today = date(2026, 6, 15)
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [
            _row(900, datetime(2026, 6, 10)),
            _row(880, datetime(2026, 6, 14)),  # new low, but above 650 bar
        ])
        fired = evaluate(conn=conn, route=route, log_path=log_path, today=today)
    assert fired == []


def test_new_low_does_not_fire_on_equal_or_higher_price(tmp_path: Path):
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"
    today = date(2026, 6, 15)
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, [
            _row(600, datetime(2026, 6, 10)),
            _row(600, datetime(2026, 6, 14)),  # equal, not below
        ])
        fired = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)
    assert fired == []


def test_new_low_and_drop_do_not_double_fire_same_pass(tmp_path: Path):
    """An itinerary meeting BOTH rules gets exactly one alert per pass."""
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"
    today = date(2026, 6, 15)
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, [
            _row(600, datetime(2026, 5, 25)),
            _row(610, datetime(2026, 5, 30)),
            _row(590, datetime(2026, 6, 5)),
            _row(595, datetime(2026, 6, 10)),
            # 465 is BOTH a new all-time low AND >15% below median 597.5
            _row(465, datetime(2026, 6, 14)),
        ])
        fired = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)
        n_rows = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    assert len(fired) == 1
    assert n_rows == 1
    assert fired[0].alert_type == "new_low"  # new_low evaluates first


def test_alert_does_not_refire_on_repeated_evaluate(tmp_path: Path):
    """Running evaluate() twice on the same data must NOT double the alerts.

    Regression for the dedup bug where every evaluate() re-appended an
    alert for any itinerary still meeting the condition, so repeated
    runs piled up dozens of duplicate rows for one signal.
    """
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"
    today = date(2026, 6, 15)
    rows = [
        _row(600, datetime(2026, 5, 25)),
        _row(610, datetime(2026, 5, 30)),
        _row(590, datetime(2026, 6, 5)),
        _row(595, datetime(2026, 6, 10)),
        _row(465, datetime(2026, 6, 14)),
    ]
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, rows)

        first = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)
        assert len(first) == 1

        # Second run over identical data must fire nothing new.
        second = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)
        assert second == []

        # Exactly one alert row persisted, not two.
        n = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        assert n == 1


def test_alert_refires_on_a_further_drop(tmp_path: Path):
    """A genuinely lower price on the same itinerary IS new signal and fires."""
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"
    today = date(2026, 6, 15)
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, [
            _row(600, datetime(2026, 5, 25)),
            _row(610, datetime(2026, 5, 30)),
            _row(590, datetime(2026, 6, 5)),
            _row(595, datetime(2026, 6, 10)),
            _row(465, datetime(2026, 6, 13)),
        ])
        first = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)
        assert len(first) == 1

        # A new, lower snapshot arrives — should fire again.
        insert_calendar_rows(conn, [_row(410, datetime(2026, 6, 14))])
        second = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)
        assert len(second) == 1
        assert second[0].price == 410


def test_no_drop_alert_below_min_observations(tmp_path: Path):
    """The median-DROP rule needs >=4 priors. With only 3, no 'drop'
    alert fires — but the price IS a new all-time low, so a new_low
    alert legitimately fires instead (that's its whole purpose)."""
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"

    today = date(2026, 6, 15)
    rows = [
        _row(600, datetime(2026, 5, 25)),
        _row(610, datetime(2026, 5, 30)),
        _row(595, datetime(2026, 6, 10)),
        _row(400, datetime(2026, 6, 14)),
    ]
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, rows)
        fired = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)

    drop_alerts = [a for a in fired if a.alert_type == "drop"]
    new_lows = [a for a in fired if a.alert_type == "new_low"]
    assert drop_alerts == []
    assert len(new_lows) == 1 and new_lows[0].price == 400


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

    # No median-DROP alert (baseline window excludes the old priors).
    # 465 < all-time min 600 though, so a new_low fires — expected.
    assert [a for a in fired if a.alert_type == "drop"] == []
    assert len([a for a in fired if a.alert_type == "new_low"]) == 1


def test_duplicate_same_second_rows_fire_one_alert(tmp_path: Path):
    """Two identical-timestamp snapshot rows for one itinerary (real
    production artifact) must fire exactly ONE new_low — the branch was
    missing the fired_keys check the drop branch always had (8 doubled
    alerts found in production 2026-07-07)."""
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"
    today = date(2026, 6, 15)
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, [
            _row(600, datetime(2026, 6, 10)),
            # Same price, same second — duplicate ingest artifact.
            _row(540, datetime(2026, 6, 14)),
            _row(540, datetime(2026, 6, 14)),
        ])
        fired = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)
        n = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    assert len(fired) == 1
    assert n == 1


def test_new_low_ignores_trivial_improvement(tmp_path: Path):
    """A 567 -> 566 render twitch is not worth a push: the new_low
    epsilon (max 5 EUR, 1%) suppresses it. Regression for the 4 one-euro
    alerts in the 2026-07-07 CI batch."""
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"
    today = date(2026, 6, 15)
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, [
            _row(567, datetime(2026, 6, 10)),
            _row(566, datetime(2026, 6, 14)),   # 1 EUR below prev min
        ])
        fired = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)
    assert fired == []


def test_new_low_fires_on_meaningful_improvement(tmp_path: Path):
    db_path = tmp_path / "t.db"
    log_path = tmp_path / "alerts.log"
    today = date(2026, 6, 15)
    with connect(db_path) as conn:
        ensure_schema(conn)
        upsert_route(conn, ROUTE)
        insert_calendar_rows(conn, [
            _row(567, datetime(2026, 6, 10)),
            _row(555, datetime(2026, 6, 14)),   # 12 EUR below -> fires
        ])
        fired = evaluate(conn=conn, route=ROUTE, log_path=log_path, today=today)
    assert len(fired) == 1 and fired[0].price == 555
