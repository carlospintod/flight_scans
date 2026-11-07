"""Optional search_window.latest_departure: a hard cap on the departure
date, independent of latest_return - min_stay."""

from dataclasses import replace
from datetime import date

import pytest

from lib.config import (ConfigError, SearchWindow, _from_dict, route_from_json,
                        route_to_yaml_dict)
from lib.planner import predict_upper_bounds
from lib.sweep import plan_windows


def _raw(**sw):
    return {
        "route": {"name": "x", "origins": ["MAD"], "destinations": ["NBO"]},
        "search_window": {"earliest_departure": "2027-01-15",
                          "latest_return": "2027-05-30", **sw},
        "stay_preferences": {"min_days": 60, "max_days": 90},
        "currency": "EUR",
        "sweep": {"cadence_days": 3},
        "alerts": {"drop_threshold_pct": 15, "baseline_window_days": 30,
                   "min_observations": 4},
    }


def test_last_departure_without_cap_is_return_minus_min_stay():
    sw = SearchWindow(date(2027, 1, 15), date(2027, 5, 30))
    assert sw.last_departure(60) == date(2027, 3, 31)
    assert sw.last_departure(0) == date(2027, 5, 30)


def test_last_departure_cap_binds_only_when_earlier():
    sw = SearchWindow(date(2027, 1, 15), date(2027, 5, 30), date(2027, 3, 1))
    assert sw.last_departure(60) == date(2027, 3, 1)
    assert sw.last_departure(100) == date(2027, 2, 19)  # min-stay wins


def test_parse_and_roundtrip():
    r = _from_dict(_raw(latest_departure="2027-03-01"))
    assert r.search_window.latest_departure == date(2027, 3, 1)
    assert route_from_json(r.to_json()) == r
    assert _from_dict(route_to_yaml_dict(r)) == r


def test_omitted_or_null_cap_serializes_without_the_key():
    for raw in (_raw(), _raw(latest_departure=None), _raw(latest_departure="")):
        r = _from_dict(raw)
        assert r.search_window.latest_departure is None
        assert "latest_departure" not in route_to_yaml_dict(r)["search_window"]


@pytest.mark.parametrize("bad", ["2027-01-14", "2027-05-31"])
def test_cap_outside_window_rejected(bad):
    with pytest.raises(ConfigError, match="latest_departure"):
        _from_dict(_raw(latest_departure=bad))


def test_sweep_windows_respect_cap():
    r = _from_dict(_raw(latest_departure="2027-03-01"))
    windows, _ = plan_windows(r, today=date(2026, 11, 7))
    assert windows
    assert max(w.outbound_end for w in windows) == date(2027, 3, 1)
    uncapped = replace(r, search_window=replace(r.search_window,
                                                latest_departure=None))
    windows_u, _ = plan_windows(uncapped, today=date(2026, 11, 7))
    assert max(w.outbound_end for w in windows_u) == date(2027, 3, 31)


def test_predict_upper_bounds_cap_narrows_window():
    kw = dict(n_origins=1, n_destinations=1, earliest_departure=date(2027, 1, 15),
              latest_return=date(2027, 5, 30), min_stay_days=60)
    # 76 vs 46 departure days -> 4 vs 3 kiwi bands of 21d.
    assert predict_upper_bounds(**kw)["kiwi"] == 4
    assert predict_upper_bounds(**kw, latest_departure=date(2027, 3, 1))["kiwi"] == 3
