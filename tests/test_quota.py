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
    METERED,
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


def test_guarded_one_way_range_search_is_metered(conn):
    """Regression (2026-07-08 cron): one_way_range_search was missing
    from METERED['kiwi'], so one-way discovery bypassed the guard —
    uncharged HTTP, no budget stop, no MONTHLY-429 floor."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()

    class _OneWay:
        source_id = "kiwi"

        def __init__(self):
            self.calls = 0

        def one_way_range_search(self, **kw):
            self.calls += 1
            return _FakeResp(options=[1])

    fake = _OneWay()
    guarded = GuardedClient(fake, ledger=ledger, source="kiwi",
                            run_id="r", search_id="s", shadow=False,
                            budget_units=1)
    guarded.one_way_range_search(origin="MAD")
    ev = conn.execute("SELECT op, result FROM spend_events").fetchone()
    assert (ev["op"], ev["result"]) == ("one_way_range_search", "ok")
    with pytest.raises(QuotaExceeded):
        guarded.one_way_range_search(origin="MAD")
    assert fake.calls == 1


def test_every_kiwi_search_method_is_metered():
    """Any public *_search method on KiwiClient that METERED['kiwi']
    doesn't list passes through the guard unmetered — the exact hole
    that let one-way discovery spend uncharged on 2026-07-08."""
    from lib.kiwi_rapidapi import KiwiClient
    search_methods = {name for name in vars(KiwiClient)
                      if name.endswith("_search")
                      and not name.startswith("_")}
    assert search_methods, "expected KiwiClient to define search methods"
    missing = search_methods - set(METERED["kiwi"])
    assert not missing, f"unmetered KiwiClient search methods: {missing}"


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


# --- M2 enforcement primitives ------------------------------------------


def _cost(*lines):
    from lib.planner import CostLine, CostVector
    return CostVector(lines=tuple(
        CostLine(source=s, units=u, kind=k, note="") for s, u, k in lines))


def test_lease_cas_single_holder(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    r1 = ledger.begin_run(trigger="cron")
    assert r1 is not None
    assert ledger.begin_run(trigger="local") is None   # lease held
    ledger.finalize_run(r1, "ok")
    r2 = ledger.begin_run(trigger="local")             # freed
    assert r2 is not None and r2 != r1


def test_reserve_monthly_guard_and_pool_short(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=30, limit_total=300,
                         origin="manual")
    run = ledger.begin_run(trigger="cron")
    # available = 30 - 15(margin) = 15. First search takes 8: fits.
    assert ledger.reserve(run, "s1", _cost(("kiwi", 8, "primary"))) is True
    # Second search wants 8 more: 30-15-8(held)=7 < 8 -> pool short.
    assert ledger.reserve(run, "s2", _cost(("kiwi", 8, "primary"))) is False
    states = {r["search_id"]: r["state"] for r in conn.execute(
        "SELECT search_id, state FROM run_reservations").fetchall()}
    assert states == {"s1": "held", "s2": "skipped"}


def test_reserve_fails_closed_without_anchor(conn):
    """No bootstrap anchor -> availability unknown -> refuse (A6)."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    run = ledger.begin_run(trigger="cron")
    assert ledger.reserve(run, "s1", _cost(("kiwi", 1, "primary"))) is False


def test_reserve_per_run_pool_caps_renders(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()   # googleflights per_run_cap=30
    run = ledger.begin_run(trigger="cron")
    assert ledger.reserve(run, "s1",
                          _cost(("googleflights", 25, "primary"))) is True
    assert ledger.reserve(run, "s2",
                          _cost(("googleflights", 10, "primary"))) is False


def test_reserve_all_or_nothing_flips_earlier_lines(conn):
    """If the second line fails, the first line's hold must not leak."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=100, limit_total=300,
                         origin="manual")
    run = ledger.begin_run(trigger="cron")
    ok = ledger.reserve(run, "s1", _cost(
        ("kiwi", 8, "primary"),
        ("serpapi", 5, "contingency"),   # no serpapi anchor -> fails
    ))
    assert ok is False
    rows = conn.execute("SELECT source, state FROM run_reservations "
                        "WHERE search_id='s1'").fetchall()
    assert {r["state"] for r in rows} == {"skipped"}


def test_expired_lease_holds_stop_counting(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=30, limit_total=300,
                         origin="manual")
    run1 = ledger.begin_run(trigger="cron", lease_minutes=0)  # instantly dead
    ledger.reserve(run1, "s1", _cost(("kiwi", 8, "primary")))
    # A new run can acquire (old lease expired) and the dead hold is free.
    run2 = ledger.begin_run(trigger="cron")
    assert run2 is not None
    assert ledger.reserve(run2, "s2", _cost(("kiwi", 8, "primary"))) is True
    assert ledger.expire_orphans() == 1


def test_settle_splits_primary_then_contingency(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=200, limit_total=300,
                         origin="manual")
    ledger.record_anchor("serpapi", remaining=200, limit_total=250,
                         origin="manual")
    run = ledger.begin_run(trigger="cron")
    assert ledger.reserve(run, "s1", _cost(
        ("googleflights", 10, "primary"),
        ("serpapi", 5, "contingency"),
    ))
    # Primary rail spent 3 googleflights; fallback spent 2 serpapi.
    for _ in range(3):
        ledger.record_spend(run_id=run, search_id="s1",
                            source="googleflights", units=1, op="point_query")
    for _ in range(2):
        ledger.record_spend(run_id=run, search_id="s1",
                            source="serpapi", units=1, op="point_query")
    ledger.settle(run, "s1")
    rows = {(r["source"], r["kind"]): (r["used_units"], r["state"])
            for r in conn.execute(
                "SELECT * FROM run_reservations WHERE search_id='s1'"
            ).fetchall()}
    assert rows[("googleflights", "primary")] == (3, "consumed")
    assert rows[("serpapi", "contingency")] == (2, "consumed")


def test_settle_releases_unused_contingency(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("serpapi", remaining=200, limit_total=250,
                         origin="manual")
    run = ledger.begin_run(trigger="cron")
    assert ledger.reserve(run, "s1", _cost(
        ("googleflights", 10, "primary"),
        ("serpapi", 5, "contingency"),
    ))
    ledger.record_spend(run_id=run, search_id="s1",
                        source="googleflights", units=7, op="point_query")
    ledger.settle(run, "s1")
    rows = {(r["source"], r["kind"]): (r["used_units"], r["state"])
            for r in conn.execute(
                "SELECT * FROM run_reservations WHERE search_id='s1'"
            ).fetchall()}
    assert rows[("googleflights", "primary")] == (7, "consumed")
    assert rows[("serpapi", "contingency")] == (0, "released")
    assert ledger.reserved_units(run, "s1", "serpapi") == 0  # closed


def test_cost_vector_includes_serpapi_contingency():
    from lib.planner import Caps, cost_vector

    class _P:  # minimal RunPlan stand-in (cost_vector reads 4 attrs)
        calls_by_source = {"kiwi": 8, "googleflights": 23,
                           "aviasales": 2, "serpapi": 0}
        followup_source = "googleflights"
        sources = ("googleflights", "serpapi", "aviasales", "kiwi")
        followup_candidates = tuple(range(23))

    cv = cost_vector(_P(), caps=Caps())
    assert cv.total("googleflights") == 23
    assert cv.total("serpapi", kind="contingency") == 7   # min(23, caps 7)
    assert cv.total("serpapi", kind="primary") == 0
    assert cv.by_source() == {"kiwi": 8, "googleflights": 23,
                              "serpapi": 7, "aviasales": 2}


def test_bootstrap_owner_idempotent(conn):
    from lib.db import bootstrap_owner
    bootstrap_owner(conn)
    bootstrap_owner(conn)
    users = conn.execute("SELECT * FROM users").fetchall()
    searches = conn.execute("SELECT * FROM searches").fetchall()
    assert len(users) == 1 and users[0]["role"] == "owner"
    assert len(searches) == 1
    assert searches[0]["search_id"] == "spain-nairobi"
    assert searches[0]["priority"] == "owner"
    assert searches[0]["is_public"] == 1
    # Bootstrap never resurrects a paused/edited search's fields.
    conn.execute("UPDATE searches SET status='paused' WHERE search_id='spain-nairobi'")
    bootstrap_owner(conn)
    assert conn.execute("SELECT status FROM searches").fetchone()[0] == "paused"


def test_guard_clients_rewraps_without_double_wrapping(conn):
    from lib.clients import guard_clients
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    fake = _FakeKiwi()
    first = guard_clients({"kiwi": fake}, ledger=ledger,
                          run_id="r1", search_id="s1", shadow=True)
    second = guard_clients(first, ledger=ledger,
                           run_id="r1", search_id="s2", shadow=True)
    second["kiwi"].range_search()
    ev = conn.execute("SELECT search_id FROM spend_events").fetchall()
    assert [e["search_id"] for e in ev] == ["s2"]     # charged once, to s2
    assert second["kiwi"]._inner is fake              # not wrapped twice


def test_owner_priority_bypasses_per_search_cap(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()   # kiwi per_search_cap=10
    ledger.record_anchor("kiwi", remaining=100, limit_total=300,
                         origin="manual")
    run = ledger.begin_run(trigger="cron")
    # 20 units > guest cap 10: guests refuse, owner reserves.
    assert ledger.reserve(run, "guest", _cost(("kiwi", 20, "primary"))) is False
    assert ledger.reserve(run, "mission", _cost(("kiwi", 20, "primary")),
                          enforce_per_search_cap=False) is True
    # Owner is still bounded by the pool itself (100-15-20=65 < 70).
    assert ledger.reserve(run, "mission2", _cost(("kiwi", 70, "primary")),
                          enforce_per_search_cap=False) is False


def test_monthly_429_floors_stale_anchor(conn):
    """A MONTHLY-quota 429 while the anchor still shows availability means
    the anchor is stale; the pool floors to 0 so the next run refuses it
    (design 6.3, observed 2026-07-07: kiwi seed anchor 298 vs real 429)."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=298, limit_total=300, origin="seed")
    assert ledger.pool_state("kiwi").provider_view == 298
    fake = _FakeKiwi(fail_with="kiwi HTTP 429: You have exceeded the MONTHLY quota")
    guarded = GuardedClient(fake, ledger=ledger, source="kiwi",
                            run_id="r", search_id="s")
    with pytest.raises(RuntimeError):
        guarded.range_search()
    assert ledger.pool_state("kiwi").provider_view == 0   # floored
    origin = conn.execute("SELECT origin FROM pool_anchors "
                          "ORDER BY anchor_id DESC LIMIT 1").fetchone()[0]
    assert origin == "quota_429_floor"


def test_rate_limit_429_does_not_floor(conn):
    """A per-second rate-limit 429 (no 'monthly') is transient — the pool
    must NOT be floored."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=200, limit_total=300, origin="seed")
    fake = _FakeKiwi(fail_with="kiwi HTTP 429: Too Many Requests (rate limit)")
    guarded = GuardedClient(fake, ledger=ledger, source="kiwi",
                            run_id="r", search_id="s")
    with pytest.raises(RuntimeError):
        guarded.range_search()
    # provider_view = 200 anchor - 1 (charged-before-call spend); the
    # point is it was NOT floored to 0 by the rate-limit 429.
    assert ledger.pool_state("kiwi").provider_view == 199


def test_needs_reset_probe_after_floor_past_reset_day(conn, monkeypatch):
    """The floor-deadlock guard: a floored pool whose anchor predates the
    expected reset day needs ONE probe call — otherwise 'never presume
    resets' keeps kiwi dead forever after the real reset (found
    2026-07-07 tracing the one-way search's first-run timeline)."""
    import lib.quota as quota_mod
    from datetime import datetime, timezone

    class _FakeNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 11, 6, 0, tzinfo=tz or timezone.utc)

    ledger = QuotaLedger(conn)
    ledger.seed_pools()   # kiwi reset_anchor_day=10
    # Floored on Jul 8 (before the Jul 10 reset).
    conn.execute(
        "INSERT INTO pool_anchors (source, baseline_remaining, limit_total, "
        "last_spend_event_id, origin, baseline_at) VALUES "
        "('kiwi', 0, 300, 0, 'quota_429_floor', '2026-07-08T05:30:00Z')")
    monkeypatch.setattr(quota_mod, "datetime", _FakeNow)
    assert ledger.needs_reset_probe("kiwi") is True
    # Fresh positive anchor (post-probe) -> no more probing.
    ledger.record_anchor("kiwi", remaining=300, limit_total=300,
                         origin="reset_probe")
    assert ledger.needs_reset_probe("kiwi") is False


def test_no_reset_probe_before_reset_day(conn, monkeypatch):
    """Floored on Wed Jul 8, checked Wed Jul 8 (before the ~10th reset):
    the pool is genuinely exhausted — no probe, floor stands."""
    import lib.quota as quota_mod
    from datetime import datetime, timezone

    class _FakeNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 8, 6, 0, tzinfo=tz or timezone.utc)

    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    conn.execute(
        "INSERT INTO pool_anchors (source, baseline_remaining, limit_total, "
        "last_spend_event_id, origin, baseline_at) VALUES "
        "('kiwi', 0, 300, 0, 'quota_429_floor', '2026-07-08T05:30:00Z')")
    monkeypatch.setattr(quota_mod, "datetime", _FakeNow)
    assert ledger.needs_reset_probe("kiwi") is False


def test_no_reset_probe_when_pool_healthy(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=250, limit_total=300,
                         origin="header")
    assert ledger.needs_reset_probe("kiwi") is False


def test_every_aviasales_price_method_is_metered():
    """Same guard as the kiwi *_search one (2026-07-08): any public
    *_prices method on AviasalesClient missing from METERED['aviasales']
    would pass through the guard unmetered."""
    from lib.aviasales_api import AviasalesClient
    price_methods = {name for name in vars(AviasalesClient)
                     if name.endswith("_prices")
                     and not name.startswith("_")}
    assert price_methods, "expected AviasalesClient to define price methods"
    missing = price_methods - set(METERED["aviasales"])
    assert not missing, f"unmetered AviasalesClient price methods: {missing}"


def test_402_payment_required_floors_pool(conn):
    """A 402 Payment Required (subscription/plan wall) floors the pool —
    RapidAPI keeps decrementing + reporting the quota header on a 402, so
    the header lies about availability. Observed 2026-07-11: kiwi showed
    286/300 'remaining' while every call 402'd. Fail closed."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=286, limit_total=300, origin="header")
    fake = _FakeKiwi(fail_with="kiwi HTTP 402: Payment required")
    guarded = GuardedClient(fake, ledger=ledger, source="kiwi",
                            run_id="r", search_id="s")
    with pytest.raises(RuntimeError):
        guarded.range_search()
    assert ledger.pool_state("kiwi").provider_view == 0   # floored
    ev = conn.execute("SELECT result FROM spend_events "
                      "ORDER BY event_id DESC LIMIT 1").fetchone()
    assert ev["result"] == "402"
    origin = conn.execute("SELECT origin FROM pool_anchors "
                          "ORDER BY anchor_id DESC LIMIT 1").fetchone()[0]
    assert origin == "quota_402_floor"


def test_402_floored_pool_probes_every_run(conn):
    """A payment-walled pool is disabled until a human fixes billing, so
    needs_reset_probe returns True regardless of the reset day — the
    liveness probe self-heals the moment the subscription is restored."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()   # kiwi reset_anchor_day=10
    ledger.record_anchor("kiwi", remaining=0, limit_total=300,
                         origin="quota_402_floor")
    # No reset-day monkeypatch needed: the 402 floor short-circuits the
    # date logic entirely.
    assert ledger.needs_reset_probe("kiwi") is True
    # A healthy re-anchor (billing fixed, probe succeeded) stops probing.
    ledger.record_anchor("kiwi", remaining=300, limit_total=300,
                         origin="reset_probe")
    assert ledger.needs_reset_probe("kiwi") is False


def test_capture_anchors_does_not_unfloor_payment_walled_pool(conn):
    """Defense-in-depth (2026-07-13): a pool floored for 402 must be
    revived ONLY by a successful reset probe, never by a passively
    captured header snapshot — a 402's frozen header lies."""
    from lib.db import record_quota
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("kiwi", remaining=286, limit_total=300, origin="header")
    ledger.floor_anchor("kiwi", origin="quota_402_floor")
    assert ledger.pool_state("kiwi").provider_view == 0
    # A lying 402 header snapshot lands during the run...
    record_quota(conn, source="kiwi", remaining=285, limit_total=300,
                 raw_json="{}")
    promoted = ledger.capture_anchors_from_snapshots("2000-01-01T00:00:00Z")
    # ...and must NOT resurrect the floored pool.
    assert ledger.pool_state("kiwi").provider_view == 0
    assert ledger.pool_state("kiwi").baseline_origin == "quota_402_floor"


def test_kiwi_capture_quota_skips_payment_gate():
    """The root fix: a 402/403 response's rate-limit header is frozen and
    must never become a quota snapshot."""
    from lib.kiwi_rapidapi import KiwiClient

    recorded = []

    class _Conn:
        def execute(self, *a, **k):
            recorded.append(a)
            class _C:
                def fetchone(self_): return None
                def fetchall(self_): return []
            return _C()

    class _Resp:
        def __init__(self, status):
            self.status_code = status
            self.headers = {"x-ratelimit-requests-remaining": "285",
                            "x-ratelimit-requests-limit": "300"}

    client = KiwiClient(api_key="k", db_conn=_Conn())
    client._capture_quota(_Resp(402))
    assert client.latest_quota is None        # nothing captured
    assert recorded == []                     # nothing written
    client._capture_quota(_Resp(200))
    assert client.latest_quota["remaining"] == 285   # 2xx still captured
