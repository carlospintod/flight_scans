"""Source-health detector (R1): reads spend_events.result + pool anchors
+ per-source stored, classifies each source. Regression home for the
2026-07-11 incident (Kiwi 402'd every call, nobody paged)."""

import json
from pathlib import Path

import pytest

from lib.db import connect, ensure_schema
from lib.health import ALARMING, SourceHealth, assess_sources, health_pushes
from lib.quota import QuotaLedger
from lib.sources import spec

# What run_batch passes as opted_in on a default scan (kiwi left out).
DEFAULT_SOURCES = ["googleflights", "serpapi", "aviasales", "searchapi"]


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


def _spend(conn, run_id, source, result, *, op="range_search", units=1,
           at="2026-07-11T00:00:00Z"):
    conn.execute(
        "INSERT INTO spend_events (run_id, search_id, source, units, op, "
        "result, spent_at) VALUES (?, 's', ?, ?, ?, ?, ?)",
        (run_id, source, units, op, result, at))


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
    # kiwi is retired (registry enabled=False), so the floor only counts
    # when the run opts kiwi back in (--sources ...,kiwi). Then a wall
    # still pages.
    health = assess_sources(conn, ledger=ledger, opted_in=["kiwi"])
    assert health["kiwi"].verdict == "payment_walled"


def test_retired_source_with_stale_402_history_is_not_an_alarm(conn):
    """The 2026-11-13 /ops shape: kiwi 402'd and was floored in July,
    then retired. Its pool row and floor stay in the ledger, but nothing
    has called it since. Old history must read `disabled`, not
    payment_walled, and moving off the old alarm must not page."""
    assert spec("kiwi") is not None and spec("kiwi").enabled is False
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    # July: every kiwi call 402s; the guard floors the pool.
    ledger.record_anchor("kiwi", remaining=286, limit_total=300, origin="header")
    _run(conn, "jul", "2026-07-11T07:00:00Z")
    for _ in range(6):
        _spend(conn, "jul", "kiwi", "402")
    ledger.floor_anchor("kiwi", origin="quota_402_floor")
    # Since then: default scans on the live rails only. Five runs, so July
    # falls out of the lookback window.
    ledger.record_anchor("serpapi", remaining=200, limit_total=250,
                         origin="account_api")
    for day in range(1, 6):
        rid = f"nov{day}"
        _run(conn, rid, f"2026-11-0{day}T05:23:00Z")
        _spend(conn, rid, "serpapi", "ok", op="point_query",
               at=f"2026-11-0{day}T05:30:00Z")
        _scan_run(conn, rid, "spain-nairobi",
                  {"serpapi": {"attempted": 1, "stored": 12}})

    health = assess_sources(conn, ledger=ledger, opted_in=DEFAULT_SOURCES)

    kiwi = health["kiwi"]
    assert kiwi.verdict == "disabled"
    assert kiwi.verdict not in ALARMING
    assert kiwi.attempts == 0
    assert "402" not in kiwi.detail
    assert health["serpapi"].verdict == "live"   # live rails untouched
    # The first run after this fix diffs against a summary that still
    # says payment_walled. Leaving an alarm is not a transition that pages.
    prior = {"kiwi": SourceHealth("kiwi", "payment_walled", 0, 0, 0)}
    summary = {"per_search": [], "source_roles": {},
               "max_consecutive_skips": 0}
    assert health_pushes(health, prior, summary) == []
    # And a later opt-in that hits the wall again DOES page.
    walled = assess_sources(conn, ledger=ledger, opted_in=["kiwi"])
    assert walled["kiwi"].verdict == "payment_walled"
    assert walled["kiwi"].worse_than(kiwi) is True


def test_enabled_source_floored_and_uncalled_is_still_payment_walled(conn):
    """The disabled exemption must not hide a real wall. The planner drops
    a floored pool, so an ENABLED source behind a 402 is never called
    again, and it must keep paging (the 2026-07-11 lesson)."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("serpapi", remaining=100, limit_total=250,
                         origin="header")
    ledger.floor_anchor("serpapi", origin="quota_402_floor")
    _run(conn, "r1", "2026-07-11T07:00:00Z")   # planner dropped serpapi
    health = assess_sources(conn, ledger=ledger)   # not opted in either
    assert health["serpapi"].verdict == "payment_walled"


def test_pool_of_source_removed_from_registry_is_disabled(conn):
    """Deleting kiwi's SourceSpec later (dead-code cleanup) must not bring
    the alarm back. Its pool row would outlive it, since seed_pools never
    deletes."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    conn.execute(
        "INSERT INTO quota_pools (source, pool_kind, period_limit, "
        "reset_anchor_day, safety_margin, per_search_cap, per_run_cap, "
        "active, updated_at) VALUES ('gone', 'monthly', 300, 10, 15, 10, "
        "NULL, 1, '2026-07-01T00:00:00Z')")
    ledger.record_anchor("gone", remaining=0, limit_total=300,
                         origin="quota_402_floor")
    _run(conn, "r1", "2026-11-01T05:23:00Z")
    assert spec("gone") is None
    health = assess_sources(conn, ledger=ledger, opted_in=DEFAULT_SOURCES)
    assert health["gone"].verdict == "disabled"


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
