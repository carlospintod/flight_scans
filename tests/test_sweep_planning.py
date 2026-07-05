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

# Fixed reference date so tests are deterministic regardless of wall clock.
# Chosen before the route's earliest_departure so nothing is past-clamped.
TODAY = date(2026, 5, 1)


def _make_route(**overrides) -> RouteConfig:
    base = dict(
        name="t",
        origins=("MAD",),
        destinations=("NBO",),
        search_window=SearchWindow(
            earliest_departure=date(2026, 6, 1),
            latest_return=date(2027, 5, 31),
        ),
        stay=StayPreferences(min_days=30, max_days=60),  # span 31 -> W_o=5
        currency="EUR",
        sweep=SweepParams(),
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
    windows, _ = plan_windows(route, today=TODAY)
    assert windows, "expected at least one window"
    for w in windows:
        assert w.combo_count() <= 200, w


def test_windows_tile_with_step_equal_to_outbound_width():
    """No-overlap tiling: step between rectangle starts == outbound width."""
    route = _make_route()
    windows = [w for w in plan_windows(route, today=TODAY)[0] if w.origin == "MAD"]
    assert len(windows) >= 2
    step_days = (windows[1].outbound_start - windows[0].outbound_start).days
    # span 31 -> W_o 5.
    assert step_days == 5
    # And rectangles don't overlap: each starts the day after the prior ends.
    assert windows[1].outbound_start == windows[0].outbound_end + _one_day()


def test_windows_cover_from_earliest_departure():
    route = _make_route()
    starts = [w.outbound_start for w in plan_windows(route, today=TODAY)[0]
              if w.origin == "MAD"]
    assert starts[0] == date(2026, 6, 1)


def test_multiple_origins_each_generate_windows():
    route = _make_route(origins=("MAD", "BCN"))
    windows, _ = plan_windows(route, today=TODAY)
    assert {w.origin for w in windows} == {"MAD", "BCN"}


def test_windows_remain_inside_search_window():
    route = _make_route()
    sw = route.search_window
    for w in plan_windows(route, today=TODAY)[0]:
        assert w.outbound_start >= sw.earliest_departure
        assert w.return_end <= sw.latest_return


def test_past_departures_clamped_with_note():
    # today AFTER earliest_departure -> the gap is excluded and noted.
    route = _make_route()
    windows, notes = plan_windows(route, today=date(2026, 8, 1))
    assert all(w.outbound_start >= date(2026, 8, 1) for w in windows)
    assert any("before today" in n for n in notes)


def test_horizon_clamp_emits_note():
    # latest_return is ~13 months out; with a mid-2026 today the tail is
    # beyond Google's 330-day horizon and must be noted.
    route = _make_route()
    _, notes = plan_windows(route, today=TODAY)
    assert any("horizon" in n for n in notes)


def _one_day():
    from datetime import timedelta
    return timedelta(days=1)
