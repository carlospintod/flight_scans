"""Quota ledger (M1 shadow mode): pools, anchors, spend, GuardedClient.

The invariant under test everywhere: PREDICTED = GUARANTEED UPPER BOUND
mechanics — charge-before-call, event-id anchor ordering, OR-IGNORE
seeding, shadow-never-refuses / enforced-refuses-at-zero.
"""

from pathlib import Path

import pytest

from lib.db import connect, ensure_schema, record_quota
from lib.quota import (
    GuardedClient,
    POOL_SEEDS,
    QuotaExceeded,
    QuotaLedger,
)


@pytest.fixture()
def conn(tmp_path: Path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        yield c


def test_seed_pools_is_idempotent_and_preserves_ops_edits(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    # The $5 Kiwi Pro switch is one UPDATE...
    conn.execute("UPDATE quota_pools SET period_limit = 20000, "
                 "safety_margin = 1000 WHERE source = 'kiwi'")
    # ...and re-seeding (every run does it) must NOT clobber it.
    ledger.seed_pools()
    row = conn.execute(
        "SELECT period_limit, safety_margin FROM quota_pools "
        "WHERE source = 'kiwi'").fetchone()
    assert (row["period_limit"], row["safety_margin"]) == (20000, 1000)
    assert conn.execute("SELECT COUNT(*) FROM quota_pools").fetchone()[0] \
        == len(POOL_SEEDS)


def test_anchor_ordering_by_event_id_not_wall_clock(conn):
    """Spend recorded BEFORE an anchor lands does not count against it,
    even inside the same second (all repo timestamps truncate to 1s)."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_spend(run_id="r", search_id="s", source="kiwi",
                        units=5, op="range_search")
    ledger.record_anchor("kiwi", remaining=290, limit_total=300,
                         origin="header")
    ledger.record_spend(run_id="r", search_id="s", source="kiwi",
                        units=2, op="range_search")
    state = ledger.pool_state("kiwi")
    # 290 (anchor) - 2 (spend after anchor) - 15 (margin); the 5 units
    # before the anchor are the provider's problem, already in its 290.
    assert state.provider_view == 288
    assert state.effective_available == 288 - 15


def test_pool_state_without_anchor_is_unknown(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    state = ledger.pool_state("kiwi")
    assert state.provider_view is None
    assert state.effective_available is None


def test_seed_anchor_from_snapshots(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    record_quota(conn, source="kiwi", remaining=250, limit_total=300,
                 raw_json="{}")
    assert ledger.seed_anchor_from_snapshots("kiwi") is True
    assert ledger.seed_anchor_from_snapshots("kiwi") is False  # once only
    assert ledger.pool_state("kiwi").provider_view == 250


def test_holds_from_live_runs_reduce_availability(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=100, limit_total=300,
                         origin="manual")
    # A live run holds 30 units; an expired-lease run holds 40 (ignored).
    conn.execute("INSERT INTO ledger_runs VALUES "
                 "('live', '2026-07-07T00:00:00Z', '2099-01-01T00:00:00Z', "
                 "NULL, 'cron', 'running', 0, 0, NULL)")
    conn.execute("INSERT INTO ledger_runs VALUES "
                 "('dead', '2026-07-07T00:00:00Z', '2000-01-01T00:00:00Z', "
                 "NULL, 'cron', 'running', 0, 0, NULL)")
    for run, units in (("live", 30), ("dead", 40)):
        conn.execute(
            "INSERT INTO run_reservations (run_id, search_id, source, kind, "
            "reserved_units, state, created_at) VALUES (?, 's', 'kiwi', "
            "'primary', ?, 'held', '2026-07-07T00:00:00Z')", (run, units))
    state = ledger.pool_state("kiwi")
    assert state.holds == 30
    assert state.effective_available == 100 - 15 - 30
    # A run computing its own availability excludes its own holds.
    assert ledger.pool_state("kiwi", exclude_run="live").holds == 0


class _FakeResp:
    def __init__(self, options):
        self.options = options


class _FakeKiwi:
    source_id = "kiwi"

    def __init__(self, fail_with: str | None = None):
        self.calls = 0
        self._fail_with = fail_with

    def range_search(self, **kwargs):
        self.calls += 1
        if self._fail_with:
            raise RuntimeError(self._fail_with)
        return _FakeResp(options=[1, 2])

    def check_quota(self):
        return {"remaining": 1}


def test_guarded_client_charges_before_call_and_marks_ok(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    fake = _FakeKiwi()
    guarded = GuardedClient(fake, ledger=ledger, source="kiwi",
                            run_id="r1", search_id="s1")
    guarded.range_search(origin="MAD")
    ev = conn.execute("SELECT * FROM spend_events").fetchall()
    assert len(ev) == 1
    assert (ev[0]["source"], ev[0]["op"], ev[0]["units"],
            ev[0]["result"]) == ("kiwi", "range_search", 1, "ok")
    assert (ev[0]["run_id"], ev[0]["search_id"]) == ("r1", "s1")
    # Unmetered methods pass through unrecorded.
    assert guarded.check_quota()["remaining"] == 1
    assert guarded.source_id == "kiwi"
    assert conn.execute("SELECT COUNT(*) FROM spend_events").fetchone()[0] == 1


def test_guarded_client_charges_failed_calls_no_refund(conn):
    """Providers meter failed requests; so do we. A 429 is charged and
    labeled — never refunded (the upper bound survives every error)."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    fake = _FakeKiwi(fail_with="kiwi HTTP 429: MONTHLY quota")
    guarded = GuardedClient(fake, ledger=ledger, source="kiwi",
                            run_id="r1", search_id="s1")
    with pytest.raises(RuntimeError):
        guarded.range_search(origin="MAD")
    ev = conn.execute("SELECT result FROM spend_events").fetchone()
    assert ev["result"] == "429"


def test_shadow_mode_never_refuses(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    fake = _FakeKiwi()
    guarded = GuardedClient(fake, ledger=ledger, source="kiwi",
                            run_id="r", search_id="s", shadow=True,
                            budget_units=1)
    guarded.range_search()
    guarded.range_search()  # over budget — shadow records, doesn't stop
    assert fake.calls == 2
    assert conn.execute("SELECT COUNT(*) FROM spend_events").fetchone()[0] == 2


def test_enforced_mode_refuses_at_zero_before_calling(conn):
    """The M2 hard stop: refuse BEFORE the HTTP call, no spend recorded."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    fake = _FakeKiwi()
    guarded = GuardedClient(fake, ledger=ledger, source="kiwi",
                            run_id="r", search_id="s", shadow=False,
                            budget_units=1)
    guarded.range_search()
    with pytest.raises(QuotaExceeded):
        guarded.range_search()
    assert fake.calls == 1                      # second call never happened
    assert conn.execute("SELECT COUNT(*) FROM spend_events").fetchone()[0] == 1


def test_empty_result_marked_empty(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()

    class _Empty:
        source_id = "kiwi"

        def range_search(self, **kw):
            return _FakeResp(options=[])

    guarded = GuardedClient(_Empty(), ledger=ledger, source="kiwi",
                            run_id=None, search_id=None)
    guarded.range_search()
    assert conn.execute("SELECT result FROM spend_events").fetchone()[0] \
        == "empty"


def test_shadow_run_lifecycle_and_spend_grouping(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    run_id = ledger.begin_shadow_run(trigger="local")
    ledger.record_spend(run_id=run_id, search_id="s1", source="kiwi",
                        units=3, op="range_search")
    ledger.record_spend(run_id=run_id, search_id="s1", source="kiwi",
                        units=1, op="range_search")
    ledger.record_spend(run_id=run_id, search_id="s2",
                        source="googleflights", units=1, op="point_query")
    assert ledger.spent_by_run(run_id) == {
        ("s1", "kiwi"): 4, ("s2", "googleflights"): 1}
    ledger.finalize_run(run_id, "ok")
    row = conn.execute("SELECT status, finished_at FROM ledger_runs "
                       "WHERE run_id = ?", (run_id,)).fetchone()
    assert row["status"] == "ok" and row["finished_at"] is not None


def test_context_manager_passthrough_stays_guarded(conn):
    """`with GuardedClient(...) as c:` must yield a GUARDED client (the
    browser client is used via the context-manager protocol)."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()

    class _CtxClient:
        source_id = "googleflights"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def point_query(self, **kw):
            return _FakeResp(options=[1])

    guarded = GuardedClient(_CtxClient(), ledger=ledger,
                            source="googleflights", run_id="r",
                            search_id="s")
    with guarded as g:
        g.point_query()
    ev = conn.execute("SELECT source, op FROM spend_events").fetchone()
    assert (ev["source"], ev["op"]) == ("googleflights", "point_query")
