"""Source-health detector (R1): reads spend_events.result + pool anchors
+ per-source stored, classifies each source. Regression home for the
2026-07-11 incident (Kiwi 402'd every call, nobody paged)."""

import json
from pathlib import Path

import pytest

from lib.db import connect, ensure_schema
from lib.health import SourceHealth, assess_sources, health_pushes
from lib.quota import QuotaLedger


@pytest.fixture()
def conn(tmp_path: Path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        yield c


def _run(conn, run_id, started_at):
    conn.execute(
        "INSERT INTO ledger_runs (run_id, started_at, lease_expires_at, "
        "trigger, status) VALUES (?, ?, '2099-01-01T00:00:00Z', 'cron', 'ok')",
        (run_id, started_at))


def _spend(conn, run_id, source, result, *, op="range_search", units=1):
    conn.execute(
        "INSERT INTO spend_events (run_id, search_id, source, units, op, "
        "result, spent_at) VALUES (?, 's', ?, ?, ?, ?, '2026-07-11T00:00:00Z')",
        (run_id, source, units, op, result))


def _scan_run(conn, run_id, route_id, results: dict):
    conn.execute(
        "INSERT INTO scan_runs (started_at, route_id, trigger, sources, "
        "status, summary_json, batch_id) VALUES "
        "('2026-07-11T00:00:00Z', ?, 'cron', 'kiwi', 'ok', ?, ?)",
        (route_id, json.dumps({"results": results}), run_id))


def test_402_run_marks_source_payment_walled(conn):
    """The exact 2026-07-11 shape: a source 402s on every call."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=286, limit_total=300, origin="header")
    _run(conn, "r1", "2026-07-11T07:00:00Z")
    for _ in range(6):
        _spend(conn, "r1", "kiwi", "402")
    _scan_run(conn, "r1", "spain-nairobi", {"kiwi": {"attempted": 6, "stored": 0}})

    health = assess_sources(conn, ledger=ledger)
    assert health["kiwi"].verdict == "payment_walled"
    assert "402" in health["kiwi"].detail


def test_floored_pool_reads_payment_walled_even_without_recent_402(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=286, limit_total=300, origin="header")
    ledger.floor_anchor("kiwi", origin="quota_402_floor")
    _run(conn, "r1", "2026-07-11T07:00:00Z")   # no spend this run (skipped)
    health = assess_sources(conn, ledger=ledger)
    assert health["kiwi"].verdict == "payment_walled"


def test_dark_source_all_errors_zero_rows(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("serpapi", remaining=100, limit_total=250, origin="header")
    _run(conn, "r1", "2026-07-11T07:00:00Z")
    for _ in range(4):
        _spend(conn, "r1", "serpapi", "error", op="point_query")
    _scan_run(conn, "r1", "spain-nairobi", {"serpapi": {"attempted": 4, "stored": 0}})
    health = assess_sources(conn, ledger=ledger)
    assert health["serpapi"].verdict == "dark"


def test_auth_fail_verdict(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("serpapi", remaining=100, limit_total=250, origin="header")
    _run(conn, "r1", "2026-07-11T07:00:00Z")
    _spend(conn, "r1", "serpapi", "auth_fail", op="point_query")
    health = assess_sources(conn, ledger=ledger)
    assert health["serpapi"].verdict == "auth_failed"


def test_live_source(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=286, limit_total=300, origin="header")
    _run(conn, "r1", "2026-07-11T07:00:00Z")
    for _ in range(3):
        _spend(conn, "r1", "kiwi", "ok")
    _scan_run(conn, "r1", "spain-nairobi", {"kiwi": {"attempted": 3, "stored": 40}})
    health = assess_sources(conn, ledger=ledger)
    assert health["kiwi"].verdict == "live"
    assert health["kiwi"].stored == 40


def test_quota_low_when_near_exhausted(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()   # kiwi per_search_cap=10, margin=15
    # 20 remaining - 15 margin = 5 effective < 2*10 -> quota_low.
    ledger.record_anchor("kiwi", remaining=20, limit_total=300, origin="header")
    _run(conn, "r1", "2026-07-11T07:00:00Z")
    _spend(conn, "r1", "kiwi", "ok")
    health = assess_sources(conn, ledger=ledger)
    assert health["kiwi"].verdict == "quota_low"


def test_idle_source_not_alarming(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("aviasales", remaining=0, limit_total=None, origin="seed")
    _run(conn, "r1", "2026-07-11T07:00:00Z")   # aviasales not called
    health = assess_sources(conn, ledger=ledger)
    assert health["aviasales"].verdict == "idle"


def test_worse_than_transition():
    live = SourceHealth("kiwi", "live", 3, 3, 40)
    walled = SourceHealth("kiwi", "payment_walled", 6, 0, 0)
    assert walled.worse_than(live) is True
    assert live.worse_than(walled) is False
    assert walled.worse_than(None) is True      # first sighting of a wall


def test_health_pushes_fire_on_transition_only():
    live = {"kiwi": SourceHealth("kiwi", "live", 3, 3, 40)}
    walled = {"kiwi": SourceHealth("kiwi", "payment_walled", 6, 0, 0,
                                   detail="402", last_ok_at="2026-07-08T00:00:00Z")}
    summary = {"per_search": [], "source_roles": {}, "max_consecutive_skips": 0}
    # live -> walled: page.
    pushes = health_pushes(walled, live, summary)
    assert any("kiwi" in p["title"] for p in pushes)
    # walled -> walled: no re-page (no worsening).
    assert health_pushes(walled, walled, summary) == []


def test_health_pushes_role_blackout_and_zero_rows():
    prior = {"aviasales": SourceHealth("aviasales", "live", 2, 2, 10)}
    current = {"aviasales": SourceHealth("aviasales", "dark", 2, 0, 0,
                                         detail="all failed")}
    summary = {
        "per_search": [{"search_id": "spain-nairobi", "status": "ok",
                        "rows_stored": 0}],
        "source_roles": {"discovery": ["aviasales"]},
        "max_consecutive_skips": 0,
    }
    pushes = health_pushes(current, prior, summary)
    titles = " ".join(p["title"] for p in pushes)
    assert "discovery" in titles      # whole-role blackout
    assert "0 rows" in titles         # ran-but-stored-nothing
