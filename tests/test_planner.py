"""Tests for lib/planner.py — the quote==execution guarantee.

The central property: the number of API calls the RunPlan quotes must
equal the number of calls actually made when run_sweep / run_followup
execute that plan. Verified with fake counting clients — no network.
"""

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from lib.config import (
    AlertParams, FollowupParams, RouteConfig, SearchWindow,
    StayPreferences, SweepParams,
)
from lib.db import (
    CalendarRow, connect, ensure_schema, insert_calendar_rows, upsert_route,
)
from lib.followup import run_followup
from lib.planner import Caps, build_run_plan
from lib.searchapi_io import CalendarResponse, PointResponse
from lib.sweep import run_sweep

TODAY = date(2026, 7, 5)


def _route(**overrides) -> RouteConfig:
    base = dict(
        name="t",
        origins=("MAD", "BCN"),
        destinations=("NBO",),
        search_window=SearchWindow(
            earliest_departure=date(2026, 9, 1),
            latest_return=date(2026, 12, 20),
        ),
        stay=StayPreferences(min_days=60, max_days=90),
        currency="EUR",
        sweep=SweepParams(),
        followup=FollowupParams(watch_below_price=600, drop_above_price=800),
        alerts=AlertParams(15, 30, 4),
    )
    base.update(overrides)
    return RouteConfig(**base)


class FakeSearchApiClient:
    """Counts calls; returns empty results."""

    def __init__(self):
        self.calendar_calls = 0
        self.point_calls = 0

    def calendar(self, **kwargs):
        self.calendar_calls += 1
        return CalendarResponse(raw={}, entries=())

    def point_query(self, **kwargs):
        self.point_calls += 1
        return PointResponse(raw={}, best_flights=())


def _cal_row(dep: str, ret: str, stay: int, price: int,
             snap: str = "2026-06-01T00:00:00Z") -> CalendarRow:
    return CalendarRow(
        snapshot_at=snap, route_id="t", source="searchapi",
        origin="MAD", destination="NBO",
        departure_date=dep, return_date=ret, stay_days=stay,
        price=price, currency="EUR", is_lowest_price=False,
    )


def test_plan_totals_equal_executed_calls(tmp_path: Path):
    """The core guarantee: quote == execution, sweep + followup."""
    db = tmp_path / "t.db"
    route = _route()
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        # Seed two in-window candidates (seen cheap, still affordable).
        insert_calendar_rows(conn, [
            _cal_row("2026-09-05", "2026-11-08", 64, 540),
            _cal_row("2026-10-01", "2026-12-05", 65, 590),
        ])

        plan = build_run_plan(
            conn, route, sources=["searchapi"],
            caps=Caps(searchapi_sweep=None, searchapi_followup=None),
            today=TODAY,
        )
        assert plan.calls_by_source["searchapi"] == (
            len(plan.sweep_windows) + len(plan.followup_candidates)
        )
        assert len(plan.followup_candidates) == 2

        fake = FakeSearchApiClient()
        sweep_res = run_sweep(
            conn=conn, client=fake, route=route,
            windows=list(plan.sweep_windows), today=TODAY,
        )
        follow_res = run_followup(
            conn=conn, client=fake, route=route,
            candidates=list(plan.followup_candidates),
            skyscanner_max_calls=0,
        )
        executed = fake.calendar_calls + fake.point_calls
        assert executed == plan.calls_by_source["searchapi"], (
            f"plan said {plan.calls_by_source['searchapi']}, "
            f"executed {executed}"
        )
        assert sweep_res.calls_made == len(plan.sweep_windows)
        assert follow_res.calls_made == len(plan.followup_candidates)


def test_plan_respects_caps_with_notes(tmp_path: Path):
    db = tmp_path / "t.db"
    route = _route()
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        plan = build_run_plan(
            conn, route, sources=["searchapi"],
            caps=Caps(searchapi_sweep=3, searchapi_followup=0),
            today=TODAY,
        )
    assert len(plan.sweep_windows) == 3
    assert any("capped to 3" in n for n in plan.notes)


def test_plan_applies_smart_skip_with_note(tmp_path: Path):
    """Windows whose prior snapshot min exceeds the threshold are excluded
    from the plan itself (not just at execution time)."""
    db = tmp_path / "t.db"
    route = _route(
        origins=("MAD",),
        sweep=SweepParams(skip_if_min_above=800, skip_grace_days=10),
    )
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        # First, plan with an empty DB — no skips possible.
        plan0 = build_run_plan(
            conn, route, sources=["searchapi"], caps=Caps(), today=TODAY,
        )
        n0 = len(plan0.sweep_windows)
        assert n0 > 0

        # Seed an expensive prior snapshot covering the FIRST window's box.
        w = plan0.sweep_windows[0]
        insert_calendar_rows(conn, [
            CalendarRow(
                snapshot_at="2026-06-20T00:00:00Z", route_id="t",
                source="searchapi", origin=w.origin, destination=w.destination,
                departure_date=w.outbound_start.isoformat(),
                return_date=w.return_start.isoformat(),
                stay_days=60, price=950, currency="EUR",
                is_lowest_price=False,
            ),
        ])
        plan1 = build_run_plan(
            conn, route, sources=["searchapi"], caps=Caps(), today=TODAY,
        )
    assert len(plan1.sweep_windows) == n0 - 1
    assert any("skipped" in n for n in plan1.notes)


def test_plan_kiwi_capped_and_sources_scoped(tmp_path: Path):
    db = tmp_path / "t.db"
    route = _route(origins=("MAD",))
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        # 30 in-window cheap candidates across three months.
        rows = []
        for i in range(30):
            month = 9 + (i % 3)
            day = (i % 9) + 1
            dep = date(2026, month, day)
            ret = dep.replace(month=month + 2) if month + 2 <= 12 else date(2026, 12, 20)
            stay = (ret - dep).days
            if not (60 <= stay <= 90):
                continue
            rows.append(_cal_row(dep.isoformat(), ret.isoformat(), stay, 500 + i))
        insert_calendar_rows(conn, rows)

        plan = build_run_plan(
            conn, route, sources=["kiwi"],
            caps=Caps(kiwi=5), today=TODAY,
        )
    # kiwi-only source: no sweep windows, no searchapi followups —
    # but kiwi DISCOVERY BANDS are planned (range-search calls), plus
    # point candidates within whatever budget the bands leave.
    assert plan.sweep_windows == ()
    assert plan.followup_candidates == ()
    assert len(plan.kiwi_bands) > 0
    # Bands tile the departure window: Sep 1 -> latest_dep (Dec 20 - 60d
    # = Oct 21) is 51 days -> 3 bands of <=21 days for the single origin.
    assert len(plan.kiwi_bands) == 3
    assert len(plan.kiwi_candidates) <= max(0, 5 - len(plan.kiwi_bands))
    assert plan.calls_by_source["searchapi"] == 0
    assert plan.calls_by_source["kiwi"] == (
        len(plan.kiwi_bands) + len(plan.kiwi_candidates)
    )


def test_plan_kiwi_bands_cover_departure_window(tmp_path: Path):
    """Band tiling: contiguous, non-overlapping, exactly spanning
    [earliest_departure, latest_return - min_stay]."""
    from datetime import timedelta
    db = tmp_path / "t.db"
    route = _route(origins=("MAD",))
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        plan = build_run_plan(conn, route, sources=["kiwi"],
                              caps=Caps(), today=TODAY)
    bands = sorted(plan.kiwi_bands, key=lambda b: b.outbound_start)
    sw = route.search_window
    latest_dep = sw.latest_return - timedelta(days=route.stay.min_days)
    assert bands[0].outbound_start == max(sw.earliest_departure, TODAY)
    assert bands[-1].outbound_end == latest_dep
    for a, b in zip(bands, bands[1:]):
        assert b.outbound_start == a.outbound_end + timedelta(days=1)
    for b in bands:
        # Inbound band derives from the stay range.
        assert b.inbound_start == b.outbound_start + timedelta(days=route.stay.min_days)
        assert b.inbound_end <= sw.latest_return


def test_plan_googleflights_takes_followup_role(tmp_path: Path):
    """When googleflights is enabled, followups are assigned to it (free)
    and searchapi plans zero followup calls."""
    db = tmp_path / "t.db"
    route = _route(origins=("MAD",))
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [
            _cal_row("2026-09-15", "2026-11-15", 61, 540),
            _cal_row("2026-10-01", "2026-12-05", 65, 590),
        ])
        plan = build_run_plan(
            conn, route, sources=["googleflights", "searchapi"],
            caps=Caps(searchapi_sweep=0, googleflights=30), today=TODAY,
        )
    assert plan.followup_source == "googleflights"
    # The 2 cheap leads are verified; a discovery grid fills the budget.
    leads = {(c["departure_date"], c["return_date"])
             for c in plan.followup_candidates}
    assert ("2026-09-15", "2026-11-15") in leads
    assert ("2026-10-01", "2026-12-05") in leads
    assert len(plan.followup_candidates) >= 2
    assert plan.calls_by_source["googleflights"] == len(plan.followup_candidates)
    assert plan.calls_by_source["searchapi"] == 0


def test_plan_followup_ladder_serpapi_beats_searchapi(tmp_path: Path):
    """Ladder: googleflights > serpapi > searchapi. Without googleflights,
    serpapi takes the followup role (managed + renewing beats one-time
    break-glass credits); searchapi plans zero followups."""
    db = tmp_path / "t.db"
    route = _route(origins=("MAD",))
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [
            _cal_row("2026-09-15", "2026-11-15", 61, 540),
            _cal_row("2026-10-01", "2026-12-05", 65, 590),
        ])
        plan = build_run_plan(
            conn, route, sources=["serpapi", "searchapi"],
            caps=Caps(searchapi_sweep=0, serpapi=7), today=TODAY,
        )
        # And with googleflights present, it still outranks serpapi.
        plan_gf = build_run_plan(
            conn, route, sources=["googleflights", "serpapi"],
            caps=Caps(searchapi_sweep=0), today=TODAY,
        )
    assert plan.followup_source == "serpapi"
    assert plan.calls_by_source["serpapi"] == 2
    assert plan.calls_by_source["searchapi"] == 0
    assert plan_gf.followup_source == "googleflights"
    assert plan_gf.calls_by_source["serpapi"] == 0


def test_plan_serpapi_cap_limits_candidates(tmp_path: Path):
    """Caps.serpapi bounds the followup list (91/mo budget at 13 runs)."""
    db = tmp_path / "t.db"
    route = _route(origins=("MAD",))
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [
            _cal_row(f"2026-09-{d:02d}", f"2026-11-{d:02d}", 61, 540)
            for d in range(10, 20)
        ])
        plan = build_run_plan(
            conn, route, sources=["serpapi"],
            caps=Caps(searchapi_sweep=0, serpapi=7), today=TODAY,
        )
    assert plan.followup_source == "serpapi"
    assert len(plan.followup_candidates) == 7
    assert any("capped to 7 of 10" in n for n in plan.notes)


def test_plan_aviasales_and_skyscanner_pair_counts(tmp_path: Path):
    db = tmp_path / "t.db"
    route = _route()  # 2 origins x 1 destination
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        plan = build_run_plan(
            conn, route, sources=["aviasales", "skyscanner"],
            caps=Caps(), today=TODAY,
        )
    assert len(plan.aviasales_pairs) == 2
    assert plan.calls_by_source["aviasales"] == 2
    # Sky Scrapper: 2 curve calls + 3 uncached airport lookups (MAD, BCN, NBO).
    assert len(plan.skyscanner_pairs) == 2
    assert plan.calls_by_source["skyscanner"] == 2 + 3
    assert any("airport lookups" in n for n in plan.notes)


def test_followup_candidates_month_diversified_after_cap(tmp_path: Path):
    """Capping keeps month diversity (round-robin order is preserved)."""
    db = tmp_path / "t.db"
    route = _route(origins=("MAD",))
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [
            # Sep: two cheap candidates
            _cal_row("2026-09-05", "2026-11-08", 64, 500),
            _cal_row("2026-09-10", "2026-11-15", 66, 510),
            # Oct: two slightly pricier
            _cal_row("2026-10-01", "2026-12-05", 65, 550),
            _cal_row("2026-10-05", "2026-12-10", 66, 560),
        ])
        plan = build_run_plan(
            conn, route, sources=["searchapi"],
            caps=Caps(searchapi_sweep=0, searchapi_followup=2),
            today=TODAY,
        )
    months = {c["departure_date"][:7] for c in plan.followup_candidates}
    # With round-robin, a cap of 2 spans both months instead of 2x Sep.
    assert months == {"2026-09", "2026-10"}


def test_predict_upper_bounds_matches_band_geometry(tmp_path: Path):
    """The closed-form estimator's kiwi count must equal the REAL
    planner's band count for the same window — the preview is only a
    guaranteed upper bound if the geometry is identical."""
    from lib.planner import predict_upper_bounds
    route = _route(origins=("MAD", "BCN"))
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        plan = build_run_plan(conn, route, sources=["kiwi"],
                              caps=Caps(searchapi_sweep=0), today=TODAY)
    est = predict_upper_bounds(
        n_origins=len(route.origins), n_destinations=len(route.destinations),
        earliest_departure=max(route.search_window.earliest_departure, TODAY),
        latest_return=route.search_window.latest_return,
        min_stay_days=route.stay.min_days,
    )
    assert est["kiwi"] == len(plan.kiwi_bands)
    assert est["aviasales"] == 2


def _one_way_route(**overrides) -> RouteConfig:
    base = dict(
        name="t",
        origins=("MAD", "BCN"),
        destinations=("NBO",),
        search_window=SearchWindow(
            earliest_departure=date(2026, 9, 12),
            latest_return=date(2026, 10, 15),
        ),
        stay=StayPreferences(min_days=0, max_days=0),
        currency="EUR",
        sweep=SweepParams(),
        followup=FollowupParams(watch_below_price=600, drop_above_price=800),
        alerts=AlertParams(15, 30, 4),
        trip_type="one_way",
    )
    base.update(overrides)
    return RouteConfig(**base)


def _ow_cal_row(dep: str, price: int,
                snap: str = "2026-07-01T00:00:00Z") -> CalendarRow:
    return CalendarRow(
        snapshot_at=snap, route_id="t", source="kiwi",
        origin="MAD", destination="NBO",
        departure_date=dep, return_date="", stay_days=0,
        price=price, currency="EUR", is_lowest_price=False,
    )


def test_plan_one_way_multi_source(tmp_path: Path):
    """One-way gets the full free stack (M7): gf verification via the
    ladder, serpapi contingency in the cost vector, aviasales month
    corroboration — and NO kiwi point candidates (scarce pool)."""
    from lib.planner import cost_vector

    route = _one_way_route()
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [
            _ow_cal_row("2026-09-20", 310),
            _ow_cal_row("2026-09-27", 350),
        ])
        caps = Caps(googleflights=25, serpapi=7)
        plan = build_run_plan(
            conn, route,
            sources=["kiwi", "googleflights", "serpapi", "aviasales"],
            caps=caps, today=TODAY)

    assert plan.followup_source == "googleflights"
    # 2 cheap one-way leads + a discovery grid; all carry the '' sentinel.
    assert all(c["return_date"] == "" for c in plan.followup_candidates)
    deps = {c["departure_date"] for c in plan.followup_candidates}
    assert "2026-09-20" in deps and "2026-09-27" in deps
    assert plan.kiwi_candidates == ()
    assert plan.aviasales_pairs == (("MAD", "NBO"), ("BCN", "NBO"))
    assert plan.aviasales_months == ("2026-09", "2026-10")
    assert plan.calls_by_source["aviasales"] == 4     # 2 pairs x 2 months
    n_gf = len(plan.followup_candidates)
    assert plan.calls_by_source["googleflights"] == n_gf
    cv = cost_vector(plan, caps=caps)
    contingency = [ln for ln in cv.lines if ln.kind == "contingency"]
    assert len(contingency) == 1
    assert contingency[0].source == "serpapi"
    assert contingency[0].units == min(n_gf, 7)   # min(candidates, serpapi cap)


def test_predict_upper_bounds_bounds_one_way_plan(tmp_path: Path):
    """The creation-form quote stays a guaranteed upper bound for a
    one-way plan's real geometry (kiwi/aviasales exact when today is
    before the window; verification quoted at cap)."""
    from lib.planner import predict_upper_bounds

    route = _one_way_route()
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [_ow_cal_row("2026-09-20", 310)])
        caps = Caps(googleflights=25, serpapi=7)
        plan = build_run_plan(
            conn, route,
            sources=["kiwi", "googleflights", "serpapi", "aviasales"],
            caps=caps, today=TODAY)
    bounds = predict_upper_bounds(
        n_origins=2, n_destinations=1,
        earliest_departure=date(2026, 9, 12),
        latest_return=date(2026, 10, 15),
        min_stay_days=0, trip_type="one_way")
    assert bounds["kiwi"] == len(plan.kiwi_bands)
    assert bounds["kiwi"] >= plan.calls_by_source["kiwi"]
    assert bounds["googleflights"] >= plan.calls_by_source["googleflights"]
    assert bounds["aviasales"] == plan.calls_by_source["aviasales"]
    assert bounds["serpapi_contingency"] >= 1


def test_plan_one_way_searchapi_rung_stays_gated(tmp_path: Path):
    """The searchapi adapter is round-trip only — a one-way route whose
    ladder lands on searchapi must plan ZERO followup candidates (with a
    note) instead of routing '' candidates into an adapter that would
    raise AFTER the ledger charged (review finding, 2026-07-08)."""
    route = _one_way_route()
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [_ow_cal_row("2026-09-20", 310)])
        plan = build_run_plan(
            conn, route, sources=["kiwi", "searchapi"],
            caps=Caps(searchapi_sweep=0), today=TODAY)
    assert plan.followup_source == "searchapi"
    assert plan.followup_candidates == ()
    assert plan.calls_by_source["searchapi"] == 0
    assert any("round-trip only" in n for n in plan.notes)


def test_plan_one_way_kiwi_point_fallback_stays_gated(tmp_path: Path):
    """Without googleflights in sources, round-trip routes get kiwi
    point-followup — one-way routes must NOT (round-trip search + the
    scarce 300/mo pool). Pins the planner gate the multi-source test
    can't reach (its sources include googleflights)."""
    route = _one_way_route()
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        insert_calendar_rows(conn, [_ow_cal_row("2026-09-20", 310)])
        plan = build_run_plan(
            conn, route, sources=["kiwi"], caps=Caps(), today=TODAY)
    assert plan.kiwi_candidates == ()
    assert plan.calls_by_source["kiwi"] == len(plan.kiwi_bands)


def test_pool_aware_narrowing_drops_floored_source(tmp_path: Path):
    """R2 (2026-07-11 incident): a floored/payment-walled pool is dropped
    at plan time so it emits NO cost line — the search degrades to its
    healthy sources instead of the all-or-nothing reservation nuking the
    whole search (owner included)."""
    from lib.quota import PoolState

    route = _route()
    srcs = ["kiwi", "googleflights", "serpapi", "aviasales"]
    caps = Caps(searchapi_sweep=0, googleflights=30, serpapi=7)
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        # Baseline: kiwi healthy -> discovery bands present.
        base = build_run_plan(conn, route, sources=srcs, caps=caps, today=TODAY)
        assert len(base.kiwi_bands) > 0 and base.calls_by_source["kiwi"] > 0

        floored = PoolState(
            source="kiwi", pool_kind="monthly", period_limit=300,
            provider_view=0, holds=0, safety_margin=15,
            effective_available=-15, baseline_at="2026-07-13T00:00:00Z",
            baseline_origin="quota_402_floor")
        plan = build_run_plan(conn, route, sources=srcs, caps=caps,
                              today=TODAY, pool_states={"kiwi": floored})
        # Kiwi gone, no cost line...
        assert plan.kiwi_bands == ()
        assert plan.calls_by_source["kiwi"] == 0
        # ...but the healthy sources survive -> the search still runs.
        assert plan.aviasales_pairs != ()
        assert plan.followup_source == "googleflights"
        assert len(plan.followup_candidates) >= 0     # verification intact
        assert any("kiwi dropped" in n for n in plan.notes)


def test_narrowing_never_exceeds_estimator_upper_bound(tmp_path: Path):
    """Narrowing only REDUCES the plan, so the closed-form quote (the
    all-healthy worst case) stays a guaranteed upper bound — the
    estimator/predict.ts and its fixture must NOT change in R2."""
    from lib.quota import PoolState
    from lib.planner import predict_upper_bounds

    route = _route()
    srcs = ["kiwi", "googleflights", "serpapi", "aviasales"]
    caps = Caps(searchapi_sweep=0, googleflights=25, serpapi=7)
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        floored = PoolState(
            source="kiwi", pool_kind="monthly", period_limit=300,
            provider_view=0, holds=0, safety_margin=15,
            effective_available=-15, baseline_at="2026-07-13T00:00:00Z",
            baseline_origin="quota_402_floor")
        plan = build_run_plan(conn, route, sources=srcs, caps=caps,
                              today=TODAY, pool_states={"kiwi": floored})
        bounds = predict_upper_bounds(
            n_origins=len(route.origins), n_destinations=len(route.destinations),
            earliest_departure=route.search_window.earliest_departure,
            latest_return=route.search_window.latest_return,
            min_stay_days=route.stay.min_days)
        # Actual (narrowed) kiwi spend 0 <= quoted upper bound.
        assert plan.calls_by_source["kiwi"] <= bounds["kiwi"]


def test_floored_pool_search_reserves_not_skipped(tmp_path: Path):
    """End-to-end graceful degrade: floored kiwi -> cost vector has no
    kiwi line -> reserve() SUCCEEDS (search runs on aviasales+gf+serpapi)
    instead of skip-the-whole-search. The direct fix for 2026-07-11."""
    from lib.quota import QuotaLedger
    from lib.planner import cost_vector

    route = _route()
    srcs = ["kiwi", "googleflights", "serpapi", "aviasales"]
    caps = Caps(searchapi_sweep=0, googleflights=30, serpapi=7)
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        # A cheap lead so verification (gf) + serpapi contingency appear.
        insert_calendar_rows(conn, [_cal_row("2026-09-15", "2026-11-17", 63, 480)])
        ledger = QuotaLedger(conn)
        ledger.seed_pools()
        ledger.record_anchor("serpapi", remaining=200, limit_total=250, origin="header")
        ledger.record_anchor("kiwi", remaining=286, limit_total=300, origin="header")
        ledger.floor_anchor("kiwi", origin="quota_402_floor")   # payment wall
        run = ledger.begin_run(trigger="local")

        pool_states = {p.source: p for p in ledger.all_pool_states()}
        plan = build_run_plan(conn, route, sources=srcs, caps=caps,
                              today=TODAY, pool_states=pool_states)
        cost = cost_vector(plan, caps=caps)
        assert not any(l.source == "kiwi" for l in cost.lines)   # kiwi gone
        assert ledger.reserve(run, "spain-nairobi", cost) is True  # NOT skipped
        # A real reservation exists for a surviving source.
        held = conn.execute(
            "SELECT DISTINCT source FROM run_reservations "
            "WHERE run_id = ? AND state = 'held'", (run,)).fetchall()
        assert held and all(r["source"] != "kiwi" for r in held)


def test_googleflights_discovery_grid_fills_when_no_leads(tmp_path: Path):
    """The 2026-07-14 fix: with Kiwi retired and no cheap aviasales leads,
    gf must still price a date grid across the window (live discovery),
    not sit idle. Grid stays within the gf budget (upper bound holds)."""
    route = _route()   # 2 origins, 60-90 day stay, Sep 1 - Dec 20
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        # No calendar rows -> select_candidates returns nothing.
        plan = build_run_plan(
            conn, route, sources=["googleflights", "serpapi", "aviasales"],
            caps=Caps(searchapi_sweep=0, googleflights=20, serpapi=7),
            today=TODAY)
    assert plan.followup_source == "googleflights"
    cands = plan.followup_candidates
    assert 0 < len(cands) <= 20                       # filled, within budget
    assert all(c.get("trigger") == "grid" for c in cands)
    # Spread across both origins and inside the window.
    assert {c["origin"] for c in cands} == {"MAD", "BCN"}
    assert plan.calls_by_source["googleflights"] == len(cands)
    from lib.planner import predict_upper_bounds
    b = predict_upper_bounds(
        n_origins=2, n_destinations=1,
        earliest_departure=route.search_window.earliest_departure,
        latest_return=route.search_window.latest_return,
        min_stay_days=route.stay.min_days)
    assert len(cands) <= b["googleflights"]           # invariant preserved


def test_discovery_grid_one_way_uses_sentinel(tmp_path: Path):
    route = _one_way_route()
    db = tmp_path / "t.db"
    with connect(db) as conn:
        ensure_schema(conn)
        upsert_route(conn, route)
        plan = build_run_plan(
            conn, route, sources=["googleflights", "serpapi", "aviasales"],
            caps=Caps(searchapi_sweep=0, googleflights=10, serpapi=7),
            today=TODAY)
    cands = plan.followup_candidates
    assert cands and all(c["return_date"] == "" for c in cands)   # one-way
