"""Property tests for the full-coverage sweep geometry.

The core guarantee: for every departure day in the (clamped) search
window and every stay length in [min_stay, max_stay], the corresponding
(departure, return) cell is covered by EXACTLY ONE planned rectangle —
no gaps (the old bug missed stays 74-90), no double-scanning.
"""

from datetime import date, timedelta

import pytest

from lib.config import (
    AlertParams, FollowupParams, RouteConfig, SearchWindow,
    StayPreferences, SweepParams,
)
from lib.sweep import COMBO_CAP, outbound_width_for_span, plan_windows

TODAY = date(2026, 5, 1)


def _route(min_days, max_days, *, earliest=date(2026, 6, 1),
           latest=date(2027, 5, 31), origins=("MAD",)) -> RouteConfig:
    return RouteConfig(
        name="t", origins=origins, destinations=("NBO",),
        search_window=SearchWindow(earliest_departure=earliest, latest_return=latest),
        stay=StayPreferences(min_days=min_days, max_days=max_days),
        currency="EUR", sweep=SweepParams(), followup=FollowupParams(),
        alerts=AlertParams(15, 30, 4),
    )


def _covers(w, dep: date, ret: date) -> bool:
    return (w.outbound_start <= dep <= w.outbound_end
            and w.return_start <= ret <= w.return_end)


@pytest.mark.parametrize("min_days,max_days", [(60, 90), (30, 60), (14, 30), (45, 45)])
def test_every_dep_stay_combo_covered_exactly_once(min_days, max_days):
    route = _route(min_days, max_days)
    windows, _ = plan_windows(route, today=TODAY)
    assert windows
    # Effective departure range after clamps.
    earliest = max(route.search_window.earliest_departure, TODAY)
    horizon = TODAY + timedelta(days=330)
    latest_dep = min(route.search_window.latest_return - timedelta(days=min_days),
                     horizon)

    dep = earliest
    checked = 0
    while dep <= latest_dep:
        for stay in range(min_days, max_days + 1):
            ret = dep + timedelta(days=stay)
            if ret > route.search_window.latest_return:
                continue  # legitimately out of window; not required to cover
            hits = sum(1 for w in windows if _covers(w, dep, ret))
            assert hits == 1, (
                f"dep={dep} stay={stay} ret={ret} covered by {hits} rectangles"
            )
            checked += 1
        dep += timedelta(days=1)
    assert checked > 0


@pytest.mark.parametrize("min_days,max_days", [(60, 90), (30, 60), (14, 30)])
def test_all_rectangles_within_combo_cap(min_days, max_days):
    windows, _ = plan_windows(_route(min_days, max_days), today=TODAY)
    for w in windows:
        assert w.combo_count() <= COMBO_CAP, (w, w.combo_count())


def test_span_31_uses_outbound_width_5():
    assert outbound_width_for_span(31) == 5      # 5*35 = 175 <= 200
    assert outbound_width_for_span(31) * (5 + 31 - 1) == 175


def test_outbound_width_monotone_and_capped():
    for span in range(1, 200):
        w = outbound_width_for_span(span)
        assert w >= 1
        assert w * (w + span - 1) <= COMBO_CAP
        # w+1 would exceed the cap (maximality)
        assert (w + 1) * (w + 1 + span - 1) > COMBO_CAP


def test_rectangles_clamped_to_horizon_with_note():
    route = _route(60, 90)
    windows, notes = plan_windows(route, today=TODAY)
    horizon = TODAY + timedelta(days=330)
    assert all(w.outbound_start <= horizon for w in windows)
    assert any("horizon" in n for n in notes)


def test_past_departures_clamped_to_today():
    route = _route(60, 90, earliest=date(2026, 1, 1))
    windows, notes = plan_windows(route, today=TODAY)
    assert all(w.outbound_start >= TODAY for w in windows)
    assert any("before today" in n for n in notes)


def test_wide_span_falls_back_to_stay_chunking():
    # span 400 (> COMBO_CAP) forces W_o=1 + stay chunks. Every rectangle
    # must still respect the cap and cover its stays.
    route = _route(10, 409)  # span 400
    windows, _ = plan_windows(route, today=TODAY)
    assert windows
    for w in windows:
        assert w.combo_count() <= COMBO_CAP
        # W_o must be 1 in the degenerate case.
        assert (w.outbound_end - w.outbound_start).days == 0


def test_empty_when_window_entirely_in_past():
    route = _route(60, 90, earliest=date(2025, 1, 1), latest=date(2025, 3, 1))
    windows, notes = plan_windows(route, today=TODAY)
    assert windows == []
    assert any("nothing to sweep" in n or "before today" in n for n in notes)
