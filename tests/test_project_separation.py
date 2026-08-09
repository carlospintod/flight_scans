"""flight_scans and Vuelazo share a repo and a database — not a budget.

The Spain-Nairobi tracker is a free-tier-only project. Vuelazo has an API
budget. Before this separation both ran on one set of source ids, so a
Vuelazo sweep spent the tracker's free SerpAPI searches and either
project's run could make the other skip its cron. These tests pin the
two boundaries that keep them apart: separate quota pools, separate run
leases.
"""

import pytest

from lib import sources
from lib.db import connect, ensure_schema
from lib.quota import SCOPE_TRACKER, SCOPE_VUELAZO, QuotaLedger
from lib.planner import CostLine, CostVector


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        yield c


# -- pools -------------------------------------------------------------

def test_vuelazo_and_tracker_have_separate_pools(conn):
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("serpapi", remaining=250, limit_total=250,
                         origin="manual")
    ledger.record_anchor("serpapi_vz", remaining=50, limit_total=50,
                         origin="seed")

    run = ledger.begin_run(trigger="test", scope=SCOPE_VUELAZO)
    assert ledger.reserve(run, "s", CostVector(
        [CostLine("serpapi_vz", 5, "primary", "verify")]))

    # Vuelazo's own pool shrinks...
    assert ledger.pool_state("serpapi_vz").holds == 5
    # ...and the tracker's does NOT (this is the whole point).
    assert ledger.pool_state("serpapi").holds == 0


def test_vuelazo_cannot_spend_past_its_own_slice(conn):
    """The tracker's 250 stays out of reach even though the provider
    account is the same one."""
    ledger = QuotaLedger(conn)
    ledger.seed_pools()
    ledger.record_anchor("serpapi", remaining=250, limit_total=250,
                         origin="manual")
    ledger.record_anchor("serpapi_vz", remaining=50, limit_total=50,
                         origin="seed")
    run = ledger.begin_run(trigger="test", scope=SCOPE_VUELAZO)
    # 50 - margin 5 = 45 available; 60 must be refused outright.
    assert ledger.reserve(run, "s", CostVector(
        [CostLine("serpapi_vz", 60, "primary", "sweep")])) is False
    assert ledger.pool_state("serpapi_vz").holds == 0


def test_backends_map_to_the_shared_adapter():
    assert sources.backend_of("serpapi_vz") == "serpapi"
    assert sources.backend_of("aviasales_vz") == "aviasales"
    assert sources.backend_of("searchapi_vz") == "searchapi"
    assert sources.backend_of("serpapi") == "serpapi"


def test_metered_ops_are_identical_to_the_backend():
    """Same calls, same worst-case units — only the pot differs."""
    from lib.quota import METERED
    assert METERED["serpapi_vz"] == METERED["serpapi"]
    assert METERED["aviasales_vz"] == METERED["aviasales"]


# -- keys --------------------------------------------------------------

def test_dedicated_key_wins_and_is_not_flagged_as_shared():
    env = {"SERPAPI_KEY": "tracker", "SERPAPI_KEY_VZ": "vuelazo"}
    assert sources.resolve_env_var("serpapi_vz", env) == ("SERPAPI_KEY_VZ", False)
    assert sources.shares_key_with_other_project("serpapi_vz", env) is False


def test_borrowing_the_trackers_key_is_visible_never_silent():
    env = {"SERPAPI_KEY": "tracker"}
    var, shared = sources.resolve_env_var("serpapi_vz", env)
    assert (var, shared) == ("SERPAPI_KEY", True)
    assert sources.shares_key_with_other_project("serpapi_vz", env) is True


def test_searchapi_vz_never_borrows_the_lifetime_credits():
    """SEARCHAPI_KEY is 100 LIFETIME credits reserved for the tracker's
    rectangle sweeps — a Vuelazo run must never touch them."""
    env = {"SEARCHAPI_KEY": "tracker-lifetime"}
    var, shared = sources.resolve_env_var("searchapi_vz", env)
    assert shared is False
    assert var == "SEARCHAPI_KEY_VZ"


def test_client_builder_uses_the_registry_key(monkeypatch):
    from lib import clients

    seen = {}

    class _Fake:
        @classmethod
        def from_env(cls, var="SERPAPI_KEY"):
            seen["var"] = var
            return cls()

    import lib.serpapi_io as serpapi_io
    monkeypatch.setattr(serpapi_io, "SerpApiClient", _Fake)
    monkeypatch.setenv("SERPAPI_KEY_VZ", "vz-key")
    out, warnings = clients.make_clients(["serpapi_vz"], None)
    assert seen["var"] == "SERPAPI_KEY_VZ"
    assert out["serpapi_vz"] is not None
    assert warnings == []


def test_client_builder_warns_loudly_when_borrowing(monkeypatch):
    from lib import clients

    class _Fake:
        @classmethod
        def from_env(cls, var="SERPAPI_KEY"):
            return cls()

    import lib.serpapi_io as serpapi_io
    monkeypatch.setattr(serpapi_io, "SerpApiClient", _Fake)
    monkeypatch.delenv("SERPAPI_KEY_VZ", raising=False)
    monkeypatch.setenv("SERPAPI_KEY", "shared")
    _, warnings = clients.make_clients(["serpapi_vz"], None)
    assert any("shared with the other project" in w for w in warnings), warnings


# -- run lease ---------------------------------------------------------

def test_the_two_projects_do_not_block_each_other(conn):
    ledger = QuotaLedger(conn)
    tracker = ledger.begin_run(trigger="cron", scope=SCOPE_TRACKER)
    vuelazo = ledger.begin_run(trigger="cron", scope=SCOPE_VUELAZO)
    assert tracker and vuelazo and tracker != vuelazo


def test_the_lease_is_still_single_run_within_a_project(conn):
    ledger = QuotaLedger(conn)
    first = ledger.begin_run(trigger="cron", scope=SCOPE_VUELAZO)
    second = ledger.begin_run(trigger="cron", scope=SCOPE_VUELAZO)
    assert first is not None
    assert second is None


def test_legacy_runs_default_to_the_tracker_scope(conn):
    """Rows written before the scope column existed must keep blocking
    tracker runs — a silent default of 'no scope' would let two batch
    runs spend the same quota simultaneously."""
    conn.execute(
        "INSERT INTO ledger_runs (run_id, started_at, lease_expires_at, "
        "trigger, status) VALUES ('legacy', '2026-08-08T00:00:00Z', "
        "'2099-01-01T00:00:00Z', 'cron', 'running')")
    ledger = QuotaLedger(conn)
    assert ledger.begin_run(trigger="cron", scope=SCOPE_TRACKER) is None
    assert ledger.begin_run(trigger="cron", scope=SCOPE_VUELAZO) is not None


def test_run_deals_and_run_batch_claim_different_scopes():
    """The wiring, not just the primitive."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parents[1]
    deals = (repo / "run_deals.py").read_text(encoding="utf-8")
    batch = (repo / "run_batch.py").read_text(encoding="utf-8")
    assert "scope=SCOPE_VUELAZO" in deals
    assert "scope=SCOPE_TRACKER" in batch
    # And run_deals spends from the _vz pools only.
    assert 'SRC_GOOGLE = "serpapi_vz"' in deals
    assert 'SRC_CACHED = "aviasales_vz"' in deals
