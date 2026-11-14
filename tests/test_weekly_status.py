"""Weekly Monday status push (plan 2026-11-14, A6), offline: the week
bounds, every line of the approved wording, a quiet week, and a dry run
that sends nothing and writes nothing."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from lib import deals_db
from lib import weekly_status as ws
from lib.config import (
    AlertParams, FollowupParams, RouteConfig, RollingWindow, SearchWindow,
    StayPreferences, SweepParams,
)
from lib.db import (
    CalendarRow, connect, ensure_schema, insert_calendar_rows,
    insert_calendar_sweep, insert_scan_run,
)

NOW = datetime(2026, 11, 23, 7, 7, tzinfo=timezone.utc)      # a Monday
WEEK = datetime(2026, 11, 16, tzinfo=timezone.utc)           # 16-22 Nov

ROUTE = RouteConfig(
    name="spain-nairobi",
    origins=("MAD",),
    destinations=("NBO",),
    search_window=SearchWindow(
        earliest_departure=date(2026, 12, 27),
        latest_return=date(2027, 6, 7),
        latest_departure=date(2027, 3, 9),
    ),
    stay=StayPreferences(min_days=30, max_days=90, step_days=7),
    currency="EUR",
    sweep=SweepParams(cadence_days=3, skip_if_min_above=800,
                      skip_grace_days=60),
    followup=FollowupParams(watch_below_price=600, drop_above_price=700),
    alerts=AlertParams(15.0, 30, 4),
)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "w.db") as c:
        ensure_schema(c)
        deals_db.ensure_deals_schema(c)
        yield c


def _explore(conn, at, origin="MAD", dest="EWR"):
    conn.execute(
        "INSERT INTO fare_observations (origin, dest, depart_date, return_date,"
        " price, currency, source, source_family, found_at, observed_at,"
        " is_verified) VALUES (?,?,'2027-03-02','2027-03-09',398,"
        "'EUR','explore','google',NULL,?,0)", (origin, dest, _iso(at)))


def _fare(source, dep, stay, price, at):
    d = date.fromisoformat(dep)
    return CalendarRow(snapshot_at=_iso(at), route_id=ROUTE.name,
                       source=source, origin="MAD", destination="NBO",
                       departure_date=dep,
                       return_date=(d + timedelta(days=stay)).isoformat(),
                       stay_days=stay, price=price, currency="EUR",
                       is_lowest_price=False)


def _run(conn, at, status):
    insert_scan_run(conn, started_at=_iso(at), finished_at=_iso(at),
                    route_id=ROUTE.name, trigger="cron", sources="serpapi",
                    rows_stored=1, alerts_fired=0, status=status)


def _sweep(conn, at, stay, status="ok"):
    insert_calendar_sweep(conn, route_id=ROUTE.name, source="serpapi_calendar",
                          origin="MAD", destination="NBO", stay_days=stay,
                          dep_start="2026-12-27", dep_end="2027-03-09",
                          swept_at=_iso(at), cells=73, status=status)


def test_the_week_is_the_last_full_monday_to_sunday():
    assert ws.week_bounds(NOW) == (WEEK, WEEK + timedelta(days=7))
    # Late on Sunday is still the week before; a late start keeps the week.
    assert ws.week_bounds(NOW - timedelta(hours=8))[0] == WEEK - timedelta(days=7)
    assert ws.week_bounds(NOW + timedelta(hours=10))[0] == WEEK
    assert ws.period_label(WEEK, WEEK + timedelta(days=7)) == "16–22 Nov"
    a = datetime(2026, 11, 30, tzinfo=timezone.utc)
    assert ws.period_label(a, a + timedelta(days=7)) == "30 Nov – 6 Dec"


def test_a_full_week_reads_like_the_approved_draft(conn):
    day = timedelta(days=1)
    # Collector: 5 of 7 days; 6 MAD-EWR rows + the 2 BCN-LIM below = 8 in
    # the week; one row before and one after it.
    for i, n in ((0, 2), (1, 1), (2, 1), (4, 1), (6, 1)):
        for _ in range(n):
            _explore(conn, WEEK + i * day + timedelta(hours=4))
    _explore(conn, WEEK - day)
    _explore(conn, WEEK + 7 * day + timedelta(hours=1))
    # Baselines: MAD-EWR has 7 rows before the week ended; BCN-LIM 2.
    _explore(conn, WEEK + day, "BCN", "LIM")
    _explore(conn, WEEK + day, "BCN", "LIM")
    # Scans: 3 ok + 1 degraded + 1 skipped (left out) in the week.
    for i, st in ((0, "ok"), (2, "ok"), (5, "ok"), (5, "degraded"),
                  (6, "skipped")):
        _run(conn, WEEK + i * day + timedelta(hours=13), st)
    _run(conn, WEEK - 2 * day, "ok")                   # last week: not counted
    # Sweep: 7 stays ok this week, one errored, one ok but last week.
    for stay in (30, 37, 44, 51, 58, 65, 72):
        _sweep(conn, WEEK + 2 * day, stay)
    _sweep(conn, WEEK + 2 * day, 79, status="error")
    _sweep(conn, WEEK - 3 * day, 86)
    insert_calendar_rows(conn, [
        # verified, this week: best 549 (the 30-day stay), last week 557
        _fare("googleflights", "2027-02-03", 70, 549, WEEK + 3 * day),
        _fare("serpapi", "2027-01-10", 30, 560, WEEK + 3 * day),
        _fare("serpapi", "2027-01-28", 82, 557, WEEK - 4 * day),
        # out of the window or stay range: never counted
        _fare("googleflights", "2026-12-10", 60, 300, WEEK + 3 * day),
        _fare("googleflights", "2027-01-05", 20, 310, WEEK + 3 * day),
        # leads (calendar cells), this week
        _fare("serpapi_calendar", "2027-01-12", 30, 480, WEEK + 2 * day),
        _fare("serpapi_calendar", "2027-01-20", 86, 612, WEEK + 2 * day),
        # aviasales is not Google family: not in "cheapest by stay"
        _fare("aviasales", "2027-01-20", 79, 400, WEEK + 2 * day),
    ])
    s = ws.build(conn, ROUTE, NOW)
    assert s.title == "Weekly status: 16–22 Nov"
    assert s.body.splitlines() == [
        "Vuelazo collector: 5 of 7 days ran, 8 fares stored.",
        "Baselines: 1 of 2 routes have 4+ observations.",
        "Nairobi: 3 of 4 scans ok; calendar sweep 7 of 10 strips.",
        "Best verified: 549 € MAD 3 Feb → 14 Apr (−8 € vs last week).",
        "Cheapest by stay: ~1 month 480 € (30 d) · ~2 months 549 € (70 d) "
        "· ~3 months 612 € (86 d).",
        "Window: departures 27 Dec – 9 Mar (as of the last scan).",
    ]


def test_a_quiet_week_still_sends_and_says_so(conn):
    s = ws.build(conn, ROUTE, NOW)
    assert s.body.splitlines() == [
        "Vuelazo collector: 0 of 7 days ran, 0 fares stored.",
        "Baselines: 0 of 0 routes have 4+ observations.",
        "Nairobi: 0 of 0 scans ok; calendar sweep 0 of 10 strips.",
        "Best verified: no verified fare in the window this week.",
        "Cheapest by stay: ~1 month — · ~2 months — · ~3 months —.",
        "Window: departures 27 Dec – 9 Mar (as of the last scan).",
    ]
    assert ws.build(conn, None, NOW).body.splitlines()[2] == (
        "Nairobi: no active owner search.")


def test_dry_run_sends_nothing_and_writes_nothing(conn, tmp_path, monkeypatch):
    from scripts import weekly_status as script
    from lib import db as db_mod
    from lib import pushes
    from lib.route_store import save_route_config

    rolling = RouteConfig(**{**ROUTE.__dict__, "search_window": SearchWindow(
        earliest_departure=date(2026, 12, 27), latest_return=date(2027, 6, 7),
        latest_departure=date(2027, 3, 9),
        rolling=RollingWindow(start_months=1, span_months=3,
                              not_before=date(2026, 12, 27)))})
    save_route_config(conn, rolling)
    from lib.db import bootstrap_owner
    bootstrap_owner(conn)
    _explore(conn, WEEK + timedelta(hours=4))
    _run(conn, datetime(2026, 11, 18, 13, tzinfo=timezone.utc), "ok")
    # Keep a developer's real Turso out (see test_watchdog).
    monkeypatch.setenv("TURSO_DATABASE_URL", "")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "")
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    real = db_mod.connect
    monkeypatch.setattr(db_mod, "connect", lambda _p: real(tmp_path / "w.db"))
    monkeypatch.setattr(pushes, "push",
                        lambda *a, **k: pytest.fail("dry run sent a push"))
    before = conn.execute("SELECT config_json FROM routes").fetchone()[0]
    out = tmp_path / "s.json"
    assert script.main(["--dry-run", "--now", _iso(NOW),
                        "--json-summary", str(out)]) == 0
    s = json.loads(out.read_text(encoding="utf-8"))
    assert s["dry_run"] and s["sent"] is False
    assert s["title"] == "Weekly status: 16–22 Nov"
    assert "1 of 7 days ran, 1 fare stored." in s["body"]
    # The window is the LAST SCAN's (Wed 18 Nov -> step Mon 16 Nov ->
    # 16 Dec floored to 27 Dec, three months from 16 Dec), as the radar
    # shows it, not the one the next scan will resolve (23 Mar).
    assert s["body"].endswith(
        "Window: departures 27 Dec – 16 Mar (as of the last scan).")
    assert "Nairobi: 1 of 1 scans ok" in s["body"]
    assert conn.execute("SELECT config_json FROM routes").fetchone()[0] == before
