"""Audit fixes of 2026-11-14 (plan B5), offline.

  * crash catch-up: rows a run stored but never evaluated (it died first)
    are alerted by the next run, never twice;
  * evaluate() runs even when a fetch step raises;
  * verification lag: a cheap calendar cell found THIS run is verified
    THIS run, inside slots the plan reserved for it;
  * F7: confidence is capped by how much of the window is priced, and a
    drop push compares the family level, not the capped one.
"""

from datetime import date, timedelta

import pytest

from lib.alerts import WINDOW_BEST, catch_up_since, evaluate
from lib.confidence import (
    ConfidenceResult, confidence_drop_push, window_coverage, with_coverage,
)
from lib.db import (
    CalendarRow, connect, ensure_schema, insert_calendar_rows,
    insert_scan_run, upsert_route,
)
from lib.planner import Caps, build_run_plan

from tests.test_calendar_sweep import FakeSerpApi, _nbo
from tests.test_window_best import (
    A, B, PRIOR, ROUTE, S1, SCAN1, SCAN2, FakeScraper, row,
)


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        upsert_route(c, ROUTE)
        yield c


def _scan_run(conn, *, finished, status="ok", route_id=ROUTE.name):
    insert_scan_run(conn, started_at=finished, finished_at=finished,
                    route_id=route_id, trigger="test", sources="x",
                    rows_stored=1, alerts_fired=0, status=status)


# -- crash catch-up -----------------------------------------------------

def test_catch_up_since_is_the_last_finished_scan(conn):
    assert catch_up_since(conn, ROUTE.name, SCAN2) == SCAN2   # none yet
    _scan_run(conn, finished="2026-11-18T09:00:00Z")
    _scan_run(conn, finished="2026-11-18T21:00:00Z", status="failed")
    _scan_run(conn, finished="2026-11-18T22:00:00Z", status="skipped")
    _scan_run(conn, finished="2026-11-18T23:00:00Z", route_id="other")
    assert catch_up_since(conn, ROUTE.name, SCAN2) == "2026-11-18T09:00:00Z"
    # Bounded: never more than CATCH_UP_MAX_DAYS back.
    assert catch_up_since(conn, ROUTE.name, "2026-12-30T00:00:00Z") == (
        "2026-12-16T00:00:00Z")


def test_rows_a_crashed_run_stored_are_alerted_by_the_next_run_once(
        conn, tmp_path):
    insert_calendar_rows(conn, [row("serpapi", A, 557, PRIOR)])
    _scan_run(conn, finished="2026-11-11T13:00:00Z")      # the 557 scan
    # Scan 1 stores a verified 549 and dies before evaluate(): no
    # scan_runs row, no alert.
    insert_calendar_rows(conn, [row("googleflights", B, 549, S1)])
    # Scan 2 stores nothing new; with its own start as `since` it would
    # miss the 549 for good.
    log = tmp_path / "alerts.log"
    today = date(2026, 11, 19)
    assert evaluate(conn=conn, route=ROUTE, log_path=log, today=today,
                    since=SCAN2) == []
    since = catch_up_since(conn, ROUTE.name, SCAN2)
    fired = evaluate(conn=conn, route=ROUTE, log_path=log, today=today,
                     since=since)
    assert [(a.alert_type, a.price, a.baseline_median) for a in fired] == [
        (WINDOW_BEST, 549, 557)]
    # Catching up from the same point again never fires it twice.
    assert evaluate(conn=conn, route=ROUTE, log_path=log, today=today,
                    since=since) == []


def test_evaluate_runs_even_when_a_fetch_step_raises(conn, tmp_path,
                                                     monkeypatch):
    import lib.runner as runner
    import lib.scanops as scanops

    insert_calendar_rows(conn, [row("serpapi", A, 557, PRIOR)])
    monkeypatch.setattr(scanops, "_now_iso", lambda: SCAN1)

    def steps(*, conn, out, **_):
        insert_calendar_rows(conn, [row("googleflights", B, 549, S1)])
        raise RuntimeError("runner killed mid-scan")

    monkeypatch.setattr(runner, "_execute_steps", steps)
    plan = build_run_plan(conn, ROUTE, sources=["googleflights"], caps=Caps(),
                          today=date(2026, 11, 17))
    with pytest.raises(RuntimeError, match="killed"):
        runner.execute_search(conn=conn, route=ROUTE, plan=plan, clients={},
                              caps=Caps(), alerts_log=tmp_path / "a.log")
    got = conn.execute("SELECT alert_type, price FROM alerts").fetchall()
    assert [tuple(r) for r in got] == [(WINDOW_BEST, 549)]


# -- verification lag ---------------------------------------------------

def test_cells_the_sweep_finds_are_verified_in_the_same_run(tmp_path):
    from lib.runner import execute_search

    route = _nbo(origins=("MAD",))
    with connect(tmp_path / "s.db") as c:
        ensure_schema(c)
        upsert_route(c, route)
        caps = Caps(serpapi_sweep=4)
        today = date.today()
        plan = build_run_plan(c, route, sources=["serpapi", "googleflights"],
                              caps=caps, today=today)
        assert plan.serpapi_sweep and plan.followup_candidates == ()
        assert plan.verify_after_sweep > 0       # slots kept for new cells
        assert plan.calls_by_source["googleflights"] == plan.verify_after_sweep
        scraper = FakeScraper({})
        res = execute_search(conn=c, route=route, plan=plan,
                             clients={"serpapi": FakeSerpApi(price=400),
                                      "googleflights": scraper},
                             caps=caps, alerts_log=tmp_path / "a.log")
        assert res.results["serpapi_calendar"]["stored"] > 0
        # The sweep's cheap cells went to the scraper in this same run,
        # never more than the plan reserved.
        assert 0 < len(scraper.calls) <= plan.verify_after_sweep


def test_no_sweep_reserves_no_extra_verification(tmp_path):
    route = _nbo(origins=("MAD",))
    with connect(tmp_path / "s.db") as c:
        ensure_schema(c)
        upsert_route(c, route)
        plan = build_run_plan(c, route, sources=["serpapi", "googleflights"],
                              caps=Caps(), today=date.today())
        assert plan.serpapi_sweep == () and plan.verify_after_sweep == 0


# -- F7 coverage --------------------------------------------------------

def test_window_coverage_counts_sampled_pairs_like_the_radar(tmp_path):
    from lib.config import SearchWindow, StayPreferences

    today = date(2026, 11, 14)
    route = _nbo(origins=("MAD",),
                 search_window=SearchWindow(
                     earliest_departure=date(2027, 1, 1),
                     latest_return=date(2027, 3, 1),
                     latest_departure=date(2027, 1, 10)),
                 stay=StayPreferences(min_days=30, max_days=44, step_days=7))
    # Stays 30, 37, 44; departures 1..10 Jan, all back by 1 Mar:
    # 10 x 3 = 30 pairs.
    with connect(tmp_path / "c.db") as c:
        ensure_schema(c)
        upsert_route(c, route)

        def cell(dep, stay, at):
            d = date.fromisoformat(dep)
            return CalendarRow(
                snapshot_at=at, route_id=route.name, source="serpapi_calendar",
                origin="MAD", destination="NBO", departure_date=dep,
                return_date=(d + timedelta(days=stay)).isoformat(),
                stay_days=stay, price=600, currency="EUR",
                is_lowest_price=False)

        insert_calendar_rows(c, [
            cell("2027-01-01", 30, "2026-11-13T00:00:00Z"),
            cell("2027-01-01", 30, "2026-11-12T00:00:00Z"),   # same pair
            cell("2027-01-02", 37, "2026-11-13T00:00:00Z"),
            cell("2027-01-03", 33, "2026-11-13T00:00:00Z"),   # unsampled stay
            cell("2027-01-04", 44, "2026-10-01T00:00:00Z"),   # stale
        ])
        assert window_coverage(c, route, today=today) == (2, 30)


def test_coverage_caps_the_level_and_says_so():
    medium = ConfidenceResult("medium", 89, ["google"], True, note="n")
    low_cov = with_coverage(medium, 20, 4030)
    assert low_cov.level == "low" and low_cov.family_level == "medium"
    assert low_cov.note.startswith(
        "20 of 4,030 date pairs priced in the last 21 days (0%) — capped at low")
    assert low_cov.as_dict()["coverage"] == {
        "priced": 20, "possible": 4030, "fresh_days": 21}
    high = ConfidenceResult("high", 97, ["google", "ota_metasearch"], True)
    assert with_coverage(high, 2000, 4030).level == "medium"   # 50%
    assert with_coverage(high, 3500, 4030).level == "high"     # 87%
    assert with_coverage(high, 0, 0).level == "high"           # not measured


def test_a_coverage_cap_never_pages_as_a_confidence_drop():
    high = ConfidenceResult("high", 97, ["google", "ota_metasearch"], True)
    capped = with_coverage(high, 10, 4030)          # level low, family high
    assert capped.level == "low"
    assert confidence_drop_push(capped, high) is None
    # A real family drop still pages, named by the family level.
    medium = with_coverage(
        ConfidenceResult("medium", 89, ["google"], True), 10, 4030)
    push = confidence_drop_push(medium, high)
    assert push and "dropped to medium" in push["title"]
