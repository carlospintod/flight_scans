"""Passive Vuelazo collector (DECISIONS amendment 2026-11-14), offline.

The fixture is a real google_travel_explore response (captured
2026-08-09). The full-run test drives run_deals.main() with the REAL
ledger and GuardedClient; every rail the passive mode must not touch is
booby-trapped, so building or calling one fails the test.
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from lib import explore_api
from lib.db import connect, ensure_schema
from lib import deals_db

FIXTURE = Path(__file__).parent / "fixtures" / "serpapi_explore_area.json"
REAL_QUOTES = explore_api.parse_explore(
    json.loads(FIXTURE.read_text(encoding="utf-8")), origin="MAD")


class _FixtureExplore:
    """ExploreClient duck-type over the real fixture. `fail` lists the
    window indexes that raise; `empty` the ones Google returns empty."""
    source_id = "explore"

    def __init__(self, fail=(), empty=()):
        self.calls = 0
        self.fail, self.empty = set(fail), set(empty)

    def explore(self, *, origin, month, area=None, currency="EUR"):
        i = self.calls
        self.calls += 1
        if i in self.fail:
            raise explore_api.ExploreError("HTTP 500")
        if i in self.empty:
            return []
        return [q.__class__(**{**q.__dict__, "origin": origin})
                for q in REAL_QUOTES]


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        deals_db.ensure_deals_schema(c)
        yield c


WINDOWS = [("MAD", "north_america", "2026-12"), ("BCN", "asia", "2027-01"),
           ("VLC", "africa", "2027-02")]


def test_collect_stores_every_window_and_reports_ok(conn):
    res = explore_api.collect_windows(_FixtureExplore(), WINDOWS, conn)
    assert res.stored == 3 * len(REAL_QUOTES) > 0
    n = conn.execute("SELECT COUNT(*) FROM fare_observations "
                     "WHERE source='explore'").fetchone()[0]
    assert n == res.stored
    assert explore_api.collect_verdict(res)[0] == "ok"


def test_partial_failure_is_a_warning_not_a_page(conn):
    res = explore_api.collect_windows(_FixtureExplore(fail=[1]), WINDOWS, conn)
    verdict, why = explore_api.collect_verdict(res)
    assert verdict == "warning" and "1 of 3" in why


def test_storing_nothing_is_an_error_even_when_calls_succeed(conn):
    """All windows empty: every call 'worked', zero rows. A collector
    that runs green while storing nothing is the silent failure."""
    res = explore_api.collect_windows(_FixtureExplore(empty=[0, 1, 2]),
                                      WINDOWS, conn)
    assert res.failed == 0 and res.stored == 0
    assert explore_api.collect_verdict(res)[0] == "error"


def test_all_failed_is_an_error(conn):
    res = explore_api.collect_windows(_FixtureExplore(fail=[0, 1, 2]),
                                      WINDOWS, conn)
    verdict, why = explore_api.collect_verdict(res)
    assert verdict == "error" and "3 failed" in why


def test_nothing_due_is_ok():
    assert explore_api.collect_verdict(explore_api.CollectResult())[0] == "ok"


def test_a_same_day_rerun_continues_todays_windows():
    origins, areas = ["MAD", "BCN", "VLC", "ALC"], ["a", "b", "c", "d", "e"]
    months = ["2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04"]
    day = date(2026, 11, 15)
    full = explore_api.windows_due_today(origins, areas, months, day=day,
                                         per_day=6, done_today=0)
    rest = explore_api.windows_due_today(origins, areas, months, day=day,
                                         per_day=6, done_today=4)
    assert len(full) == 6 and rest == full[4:]
    assert explore_api.windows_due_today(origins, areas, months, day=day,
                                         per_day=6, done_today=6) == []


def test_daily_budget_fits_the_pool():
    """6/day for a 31-day month must stay inside serpapi_vz's usable
    pool (limit - margin) with room for retries."""
    from lib.dealconfig import load_deal_config
    from lib.sources import POOL_SEEDS
    per_day = load_deal_config().explore_calls_per_day
    seed = next(s for s in POOL_SEEDS if s[0] == "serpapi_vz")
    limit, margin, per_search_cap = seed[2], seed[4], seed[5]
    assert per_day * 31 <= limit - margin - 30
    assert per_day <= per_search_cap


# ---- the whole run_deals.main() in collect-only mode ----------------------

class _SerpVz:
    """serpapi_vz client: only /account (unmetered) may be touched."""
    def check_quota(self):
        return {"remaining": 240, "limit_total": 250}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        raise AssertionError(f"collect-only touched serpapi_vz.{name}")


def _run_main(monkeypatch, tmp_path, explore_client, *, extra_argv=()):
    import run_deals
    from lib import clients as clients_mod
    from lib import db as db_mod

    for var in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "NTFY_TOPIC"):
        monkeypatch.delenv(var, raising=False)
    real_connect = db_mod.connect
    monkeypatch.setattr(db_mod, "connect",
                        lambda _p: real_connect(tmp_path / "run.db"))

    def _make_clients(sources, conn, **kw):
        assert sources == ["serpapi_vz"], sources
        return {"serpapi_vz": _SerpVz()}, []
    monkeypatch.setattr(clients_mod, "make_clients", _make_clients)
    monkeypatch.setattr(explore_api.ExploreClient, "from_env",
                        classmethod(lambda cls, **kw: explore_client))

    def _trap(*a, **k):
        raise AssertionError("collect-only built a publish/draft rail")
    monkeypatch.setattr(run_deals, "_build_service_rails", _trap)
    from lib import dealpipe, dealgate
    monkeypatch.setattr(dealpipe, "sweep_origin", _trap)
    monkeypatch.setattr(dealpipe, "watchlist_refresh", _trap)
    monkeypatch.setattr(dealgate, "gate_candidates", _trap)
    from lib import pushes
    monkeypatch.setattr(pushes, "push", _trap)

    out = tmp_path / "summary.json"
    monkeypatch.setattr(sys, "argv", ["run_deals.py", "--collect-only",
                                      "--json-summary", str(out),
                                      *extra_argv])
    code = run_deals.main()
    summary = json.loads(out.read_text(encoding="utf-8"))
    with real_connect(tmp_path / "run.db") as c:
        spend = [dict(r) for r in c.execute(
            "SELECT source, op, units FROM spend_events").fetchall()]
        stored = c.execute("SELECT COUNT(*) FROM fare_observations "
                           "WHERE source='explore'").fetchone()[0]
        rr = [dict(r) for r in c.execute(
            "SELECT source, reserved_units FROM run_reservations").fetchall()]
    return code, summary, spend, stored, rr


def test_collect_only_run_end_to_end(monkeypatch, tmp_path):
    from lib.dealconfig import load_deal_config
    per_day = load_deal_config().explore_calls_per_day
    fake = _FixtureExplore()
    code, summary, spend, stored, rr = _run_main(monkeypatch, tmp_path, fake)
    assert code == 0, summary
    assert summary["mode"] == "collect_only"
    assert summary["steps"]["collect"]["verdict"] == "ok"
    assert fake.calls == per_day
    # Every metered call charged before it ran, on serpapi_vz, op explore.
    assert spend and {(s["source"], s["op"]) for s in spend} == {
        ("serpapi_vz", "explore")}
    assert sum(s["units"] for s in spend) == per_day
    assert rr == [{"source": "serpapi_vz", "reserved_units": per_day}]
    assert stored == per_day * len(REAL_QUOTES)


def test_collect_only_run_that_stores_nothing_goes_red(monkeypatch, tmp_path):
    from lib.dealconfig import load_deal_config
    per_day = load_deal_config().explore_calls_per_day
    fake = _FixtureExplore(fail=range(per_day))
    code, summary, spend, stored, _ = _run_main(monkeypatch, tmp_path, fake)
    assert code != 0
    assert summary["status"] == "failed"
    assert "0 Explore rows stored" in summary["error"]
    assert stored == 0
