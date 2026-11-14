"""Dead-man watchdog (plan 2026-11-14, A5): fresh, stale, never-ran,
and the push cadence (transition, repeat every few days, recovery)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from lib import deals_db
from lib import watchdog as wd
from lib.db import connect, ensure_schema
from lib.quota import QuotaLedger

NOW = datetime(2026, 11, 20, 19, 37, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "w.db") as c:
        ensure_schema(c)
        deals_db.ensure_deals_schema(c)
        yield c


def _explore_row(conn, at):
    conn.execute(
        "INSERT INTO fare_observations (origin, dest, depart_date, return_date,"
        " price, currency, source, source_family, found_at, observed_at,"
        " is_verified) VALUES ('MAD','EWR','2027-03-02','2027-03-09',398,"
        "'EUR','explore','google',NULL,?,0)", (_iso(at),))


def _scan(conn, at, status="ok"):
    conn.execute(
        "INSERT INTO scan_runs (started_at, route_id, trigger, sources, status)"
        " VALUES (?, 'spain-nairobi', 'cron', 'serpapi', ?)", (_iso(at), status))


def _strip(conn, at, status, stay=37, origin="MAD"):
    conn.execute(
        "INSERT INTO calendar_sweeps (route_id, source, origin, destination,"
        " stay_days, dep_start, dep_end, swept_at, cells, status) VALUES"
        " ('spain-nairobi','serpapi_calendar',?,'NBO',?,'2026-12-27',"
        "'2027-03-01',?,0,?)", (origin, stay, _iso(at), status))


def _by_id(findings):
    return {f.check_id: f for f in findings}


def test_never_ran_is_bad_for_both_projects(conn):
    f = _by_id(wd.evaluate(conn, NOW))
    assert f["vuelazo_collector"].bad and "ever been stored" in f["vuelazo_collector"].detail
    assert f["nbo_scan"].bad
    assert not f["nbo_calendar"].bad
    assert not f["pool:serpapi"].bad          # no anchor: not judged


def test_fresh_is_ok(conn):
    _explore_row(conn, NOW - timedelta(hours=20))
    _scan(conn, NOW - timedelta(days=2))
    f = _by_id(wd.evaluate(conn, NOW))
    assert not f["vuelazo_collector"].bad and not f["nbo_scan"].bad


def test_stale_collector_and_scan(conn):
    _explore_row(conn, NOW - timedelta(hours=49))
    _scan(conn, NOW - timedelta(days=5))
    _scan(conn, NOW - timedelta(days=1), status="failed")   # ran, but failed
    f = _by_id(wd.evaluate(conn, NOW))
    assert f["vuelazo_collector"].bad and "49 h" in f["vuelazo_collector"].detail
    assert f["nbo_scan"].bad and "(failed)" in f["nbo_scan"].detail


def test_a_strip_failing_twice_is_bad_once_is_not(conn):
    _strip(conn, NOW - timedelta(days=10), "error", stay=37)
    _strip(conn, NOW - timedelta(days=3), "error", stay=37)
    _strip(conn, NOW - timedelta(days=10), "ok", stay=44)
    _strip(conn, NOW - timedelta(days=3), "error", stay=44)
    f = _by_id(wd.evaluate(conn, NOW))["nbo_calendar"]
    assert f.bad and "MAD→NBO 37d" in f.detail and "44d" not in f.detail
    assert "1 of 2 strips" in f.detail


def test_old_strips_age_out_of_the_check(conn):
    _strip(conn, NOW - timedelta(days=45), "error")
    _strip(conn, NOW - timedelta(days=40), "error")
    assert not _by_id(wd.evaluate(conn, NOW))["nbo_calendar"].bad


def _pool(conn, source, remaining, renewal, spent_per_day):
    led = QuotaLedger(conn)
    led.seed_pools()
    led.record_anchor(source, remaining=remaining, limit_total=250,
                      origin="account_api")
    conn.execute("INSERT INTO quota_snapshots (checked_at, source, remaining,"
                 " limit_total, raw_json) VALUES (?,?,?,?,?)",
                 (_iso(NOW), source, remaining, 250,
                  json.dumps({"plan_renewal_date": renewal})))
    # Burn AFTER the anchor so the ledger's effective view includes it.
    for d in range(wd.BURN_WINDOW_DAYS):
        conn.execute("INSERT INTO spend_events (spent_at, source, op, units,"
                     " run_id, search_id, result) VALUES (?,?,?,?,?,?,?)",
                     (_iso(NOW - timedelta(days=d, minutes=-1)), source,
                      "explore", spent_per_day, "r", "s", "ok"))


def test_pool_that_runs_out_before_renewal(conn):
    _pool(conn, "serpapi_vz", remaining=120, renewal="2026-12-09",
          spent_per_day=6)
    f = _by_id(wd.evaluate(conn, NOW))["pool:serpapi_vz"]
    assert f.bad, f.detail
    assert "2026-12-09" in f.title


def test_pool_with_room_is_ok(conn):
    _pool(conn, "serpapi_vz", remaining=250, renewal="2026-12-09",
          spent_per_day=2)
    assert not _by_id(wd.evaluate(conn, NOW))["pool:serpapi_vz"].bad


def test_push_on_transition_then_every_few_days_then_recovery(conn):
    _scan(conn, NOW - timedelta(days=1))
    _explore_row(conn, NOW - timedelta(hours=60))       # stale collector

    def tick(now):
        pushes, rows = wd.plan_pushes(conn, wd.evaluate(conn, now), now)
        wd.save_state(conn, rows)
        return [p.title for p in pushes]

    first = tick(NOW)
    assert len(first) == 1 and "colector" in first[0]
    assert tick(NOW + timedelta(days=1)) == []          # no daily spam
    assert tick(NOW + timedelta(days=2)) == []
    again = tick(NOW + timedelta(days=3))
    assert len(again) == 1
    _explore_row(conn, NOW + timedelta(days=3, hours=1))
    _scan(conn, NOW + timedelta(days=3, hours=2))
    back = tick(NOW + timedelta(days=3, hours=3))
    assert back == ["Watchdog: vuelazo_collector OK otra vez"]
    assert tick(NOW + timedelta(days=4)) == []


def test_dry_run_leaves_state_untouched(conn, tmp_path, monkeypatch):
    """The forced-stale demonstration path: pushes are printed, nothing
    is sent, the state machine does not move."""
    import sys
    sys.path.insert(0, str(tmp_path))
    from scripts import watchdog as script
    from lib import db as db_mod
    from lib import pushes

    # The script loads .env; an empty value keeps a developer's real
    # Turso URL out (load_dotenv never overrides a set variable).
    monkeypatch.setenv("TURSO_DATABASE_URL", "")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "")
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    _explore_row(conn, NOW - timedelta(days=5))         # stale, locally
    real = db_mod.connect
    db_path = tmp_path / "w.db"
    monkeypatch.setattr(db_mod, "connect", lambda _p: real(db_path))
    monkeypatch.setattr(pushes, "push",
                        lambda *a, **k: pytest.fail("dry run sent a push"))
    out = tmp_path / "s.json"
    assert script.main(["--dry-run", "--now", "2026-11-20T19:37:00Z",
                        "--json-summary", str(out)]) == 0
    s = json.loads(out.read_text(encoding="utf-8"))
    assert s["dry_run"] and s["sent"] == 0 and s["pushes"]
    assert any("colector" in p["title"] for p in s["pushes"])
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE "
                        "name='watchdog_state'").fetchone()[0] == 0
