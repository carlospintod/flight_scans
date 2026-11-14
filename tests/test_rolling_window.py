"""Rolling Nairobi window + sampled stays (plan 2026-11-14, B1-B3).

Carlos, 2026-11-14: departures "three months, from next month", never
before 27 Dec 2026, no end date; stays 30-90 days in weekly steps.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from lib.config import (
    ConfigError, RollingWindow, StayPreferences, add_months, load_route,
    route_from_json,
)
from lib.sweep import SERPAPI_CALENDAR_MAX_DEPARTURE_DAYS, plan_strips

REPO = Path(__file__).resolve().parents[1]
NBO = REPO / "routes" / "spain-nairobi.yaml"
SPEC = RollingWindow(start_months=1, span_months=3,
                     not_before=date(2026, 12, 27), step_days=7)


def _resolve(today):
    sw = SPEC.resolve(today, 90)
    return sw.earliest_departure, sw.latest_departure, sw.latest_return


@pytest.mark.parametrize("today, first, last", [
    # Sat 14 Nov: step date Mon 9 Nov -> 9 Dec, floored to 27 Dec.
    (date(2026, 11, 14), date(2026, 12, 27), date(2027, 3, 9)),
    # The step boundary: Sunday still on the 9 Nov step, Monday moves.
    (date(2026, 11, 15), date(2026, 12, 27), date(2027, 3, 9)),
    (date(2026, 11, 16), date(2026, 12, 27), date(2027, 3, 16)),
    # Floor still binding on 23 Nov (23 Dec < 27 Dec) ...
    (date(2026, 11, 23), date(2026, 12, 27), date(2027, 3, 23)),
    # ... and released on 30 Nov (30 Dec > 27 Dec).
    (date(2026, 11, 30), date(2026, 12, 30), date(2027, 3, 30)),
    # No end date: years out it keeps rolling.
    (date(2027, 5, 3), date(2027, 6, 3), date(2027, 9, 3)),
    (date(2029, 1, 1), date(2029, 2, 1), date(2029, 5, 1)),
])
def test_resolution_at_several_dates(today, first, last):
    got_first, got_last, got_ret = _resolve(today)
    assert (got_first, got_last) == (first, last)
    assert got_ret == last + timedelta(days=90)


def test_the_window_only_moves_on_mondays_and_never_before_the_floor():
    prev = None
    d = date(2026, 11, 1)
    while d < date(2028, 12, 31):
        first, last, _ = _resolve(d)
        assert first >= date(2026, 12, 27)
        assert (last - first).days + 1 <= SERPAPI_CALENDAR_MAX_DEPARTURE_DAYS
        if prev is not None and (first, last) != prev:
            assert d.weekday() == 0, d          # changes only on Mondays
        prev = (first, last)
        d += timedelta(days=1)


def test_month_arithmetic_clamps_to_month_end():
    assert add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)


def test_route_and_config_json_agree_on_the_resolved_dates():
    """The YAML, the routes row (to_json) and a re-read of that row must
    resolve the same window on the same day, and the stored dates are
    exactly today's resolution (what the web radar reads)."""
    today = date(2026, 11, 16)
    route = load_route(NBO, today=today)
    stored = json.loads(route.to_json())
    assert stored["search_window"]["earliest_departure"] == "2026-12-27"
    assert stored["search_window"]["latest_departure"] == "2027-03-16"
    assert stored["search_window"]["latest_return"] == "2027-06-14"
    again = route_from_json(route.to_json(), today=today)
    assert again == route
    # A week later the same row resolves the next step: stored dates are
    # a cache, never a pin.
    later = route_from_json(route.to_json(), today=date(2026, 11, 23))
    assert later.search_window.latest_departure == date(2027, 3, 23)


def test_route_store_rewrites_the_row_when_the_window_moves(tmp_path):
    from lib.db import connect, ensure_schema
    from lib.route_store import get_route_config_json, load_effective_route
    from lib.route_store import save_route_config

    with connect(tmp_path / "r.db") as conn:
        ensure_schema(conn)
        save_route_config(conn, load_route(NBO, today=date(2026, 11, 16)))
        import lib.config as cfg_mod
        real_date = cfg_mod.date

        class _Monday(real_date):
            @classmethod
            def today(cls):
                return real_date(2026, 11, 23)
        cfg_mod.date = _Monday
        try:
            route, src = load_effective_route(conn, "spain-nairobi", REPO / "routes")
        finally:
            cfg_mod.date = real_date
        assert src == "db"
        assert route.search_window.latest_departure == date(2027, 3, 23)
        row = json.loads(get_route_config_json(conn, "spain-nairobi"))
        assert row["search_window"]["latest_departure"] == "2027-03-23"


def test_rolling_is_validated():
    base = json.loads(load_route(NBO, today=date(2026, 11, 14)).to_json())
    for bad in ({"start_months": -1, "span_months": 3},
                {"start_months": 1, "span_months": 0},
                {"start_months": 1, "span_months": 3, "step_days": 0},
                {"start_months": 1, "span_months": 3, "not_before": "soon"}):
        raw = json.loads(json.dumps(base))
        raw["search_window"]["rolling"] = bad
        with pytest.raises(ConfigError):
            route_from_json(json.dumps(raw))


# ---- sampled stays (B2) ----------------------------------------------------

def test_sampled_stays():
    assert StayPreferences(30, 90, 7).sampled() == (
        30, 37, 44, 51, 58, 65, 72, 79, 86, 90)
    assert StayPreferences(60, 90).sampled() == tuple(range(60, 91))
    assert StayPreferences(30, 30, 7).sampled() == (30,)
    assert StayPreferences(30, 37, 7).sampled() == (30, 37)


def test_the_sweep_plans_one_strip_per_sampled_stay_and_origin():
    today = date(2026, 11, 14)
    route = load_route(NBO, today=today)
    strips = plan_strips(route, today=today)
    assert len(strips) == 20                        # 10 stays x 2 origins
    assert sorted({s.stay_days for s in strips}) == list(route.stay.sampled())
    for s in strips:
        assert s.dep_start == date(2026, 12, 27)
        assert s.dep_end == date(2027, 3, 9)        # whole window, one call
    # At the widest window (93 departure days) it is still one call each.
    wide_day = date(2027, 6, 7)                     # step 7 Jun: 7 Jul..7 Oct
    wide = load_route(NBO, today=wide_day)
    ws = plan_strips(wide, today=wide_day)
    assert len(ws) == 20 and all(s.departures <= 93 for s in ws)


def test_the_93_day_probe_fixture_is_whole():
    """B3, 2026-11-14: one credit priced all 93 departure days."""
    from lib.serpapi_io import _parse_calendar_prices
    raw = json.loads((REPO / "tests" / "fixtures"
                      / "serpapi_calendar_93day.json").read_text(encoding="utf-8"))
    entries = _parse_calendar_prices(raw)
    prices = raw["prices"]
    deps = sorted({p["outbound_date"] for p in prices})
    assert len(deps) == 93 and deps[0] == "2026-12-27" and deps[-1] == "2027-03-29"
    assert all(isinstance(p.get("price"), int) for p in prices)
    assert raw["search_parameters"]["round_trip_days"] == 30
    assert "api_key" not in json.dumps(raw)
    assert len(entries) == 93 and all(e.price for e in entries)
    assert SERPAPI_CALENDAR_MAX_DEPARTURE_DAYS == 93


def test_discovery_grid_only_uses_sampled_stays(tmp_path):
    from lib.planner import _discovery_grid
    today = date(2026, 11, 14)
    route = load_route(NBO, today=today)
    seen = set()
    for k in range(30):
        for p in _discovery_grid(route, today=today + timedelta(days=k),
                                 max_points=5):
            d = date.fromisoformat(p["departure_date"])
            r = date.fromisoformat(p["return_date"])
            seen.add((r - d).days)
    assert seen <= set(route.stay.sampled()), seen
    assert len(seen) >= 5


def test_candidates_skip_unsampled_stays(tmp_path):
    from lib.db import connect, ensure_schema
    from lib.followup import select_candidates
    today = date(2026, 11, 14)
    route = load_route(NBO, today=today)
    with connect(tmp_path / "c.db") as conn:
        ensure_schema(conn)
        for stay, price in ((37, 550), (40, 500)):     # 40 is not sampled
            dep = date(2027, 1, 10)
            conn.execute(
                "INSERT INTO calendar_snapshots (snapshot_at, route_id, source,"
                " origin, destination, departure_date, return_date, stay_days,"
                " price, currency, is_lowest_price) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                ("2026-11-14T12:00:00Z", "spain-nairobi", "serpapi_calendar",
                 "MAD", "NBO", dep.isoformat(),
                 (dep + timedelta(days=stay)).isoformat(), stay, price, "EUR"))
        cands = select_candidates(conn, route, today=today)
    stays = {(date.fromisoformat(c["return_date"])
              - date.fromisoformat(c["departure_date"])).days for c in cands}
    assert stays == {37}
