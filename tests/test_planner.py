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
    assert len(plan.followup_candidates) == 2
    assert plan.calls_by_source["googleflights"] == 2
    assert plan.calls_by_source["searchapi"] == 0


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
