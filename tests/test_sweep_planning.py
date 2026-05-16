from datetime import date

from lib.config import (
    AlertParams,
    FollowupParams,
    RouteConfig,
    SearchWindow,
    StayPreferences,
    SweepParams,
)
from lib.sweep import plan_windows


def _make_route(**overrides) -> RouteConfig:
    base = dict(
        name="t",
        origins=("MAD",),
        destinations=("NBO",),
        search_window=SearchWindow(
            earliest_departure=date(2026, 6, 1),
            latest_return=date(2027, 5, 31),
        ),
        stay=StayPreferences(min_days=30, max_days=60),
        currency="EUR",
        sweep=SweepParams(
            outbound_window_days=14,
            return_window_days=14,
            overlap_days=3,
            cadence_days=14,
        ),
        followup=FollowupParams(),
        alerts=AlertParams(
            drop_threshold_pct=15,
            baseline_window_days=30,
            min_observations=4,
        ),
    )
    base.update(overrides)
    return RouteConfig(**base)


def test_windows_respect_calendar_combo_cap():
    route = _make_route()
    windows = plan_windows(route)
    assert windows, "expected at least one window"
    for w in windows:
        assert w.combo_count() <= 200, w


def test_windows_slide_with_step_equal_to_window_minus_overlap():
    route = _make_route()
    windows = [w for w in plan_windows(route) if w.origin == "MAD"]
    assert len(windows) >= 2
    step_days = (windows[1].outbound_start - windows[0].outbound_start).days
    # 14 outbound - 3 overlap = 11 day step
    assert step_days == 11


def test_windows_cover_entire_search_window():
    route = _make_route()
    starts = [w.outbound_start for w in plan_windows(route) if w.origin == "MAD"]
    assert starts[0] == date(2026, 6, 1)
    # Last outbound start should be within (latest_return - min_stay - step).
    assert starts[-1] >= date(2027, 4, 1)
    assert starts[-1] <= date(2027, 5, 1)


def test_multiple_origins_each_generate_windows():
    route = _make_route(origins=("MAD", "BCN"))
    windows = plan_windows(route)
    origins = {w.origin for w in windows}
    assert origins == {"MAD", "BCN"}


def test_windows_remain_inside_search_window():
    route = _make_route()
    sw = route.search_window
    for w in plan_windows(route):
        assert w.outbound_start >= sw.earliest_departure
        assert w.return_end <= sw.latest_return
