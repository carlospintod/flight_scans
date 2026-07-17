from datetime import date
from pathlib import Path

import pytest

from lib.config import ConfigError, load_route


REPO = Path(__file__).resolve().parents[1]


def test_loads_first_run_yaml():
    cfg = load_route(REPO / "routes" / "spain-nairobi.yaml")
    assert cfg.name == "spain-nairobi"
    assert cfg.origins == ("MAD", "BCN")
    assert cfg.destinations == ("NBO",)
    assert cfg.currency == "EUR"
    assert cfg.stay.min_days == 60
    assert cfg.stay.max_days == 90
    # Window-size keys are legacy; the real-trip YAML omits them and the
    # loader falls back to the harmless defaults.
    assert cfg.sweep.cadence_days == 3
    assert cfg.sweep.skip_if_min_above == 800
    assert cfg.sweep.skip_grace_days == 60
    # 2026-07-16 coverage audit: watch raised 500->600 (nothing had ever
    # qualified; all-time min was 532), drop 700 keeps the 100-EUR
    # hysteresis. YAML synced from the canonical DB config.
    assert cfg.followup.watch_below_price == 600
    assert cfg.followup.drop_above_price == 700
    assert cfg.alerts.drop_threshold_pct == 15
    assert cfg.alerts.baseline_window_days == 30
    assert cfg.alerts.min_observations == 4
    # The REAL trip window: mid-Sep departures, returns through mid-Jan.
    assert cfg.search_window.earliest_departure == date(2026, 9, 12)
    assert cfg.search_window.latest_return == date(2027, 1, 15)


def test_legacy_oversized_window_keys_no_longer_rejected(tmp_path):
    """The 200-combo validation on window-size keys is gone — geometry is
    now derived from the stay range by the planner, so a legacy YAML with
    an 'oversized' 20x14 window must load fine."""
    p = tmp_path / "legacy.yaml"
    p.write_text(
        "route:\n  name: x\n  origins: [MAD]\n  destinations: [NBO]\n"
        "search_window:\n  earliest_departure: 2026-06-01\n  latest_return: 2027-05-31\n"
        "stay_preferences: {min_days: 30, max_days: 60}\n"
        "currency: EUR\n"
        "sweep: {outbound_window_days: 20, return_window_days: 14, overlap_days: 3, cadence_days: 14}\n"
        "alerts: {drop_threshold_pct: 15, baseline_window_days: 30, min_observations: 4}\n"
    )
    cfg = load_route(p)  # must not raise
    assert cfg.stay.min_days == 30


def test_rejects_bad_iata_code(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "route:\n  name: x\n  origins: [MADRID]\n  destinations: [NBO]\n"
        "search_window:\n  earliest_departure: 2026-06-01\n  latest_return: 2027-05-31\n"
        "stay_preferences: {min_days: 30, max_days: 60}\n"
        "currency: EUR\n"
        "sweep: {outbound_window_days: 14, return_window_days: 14, overlap_days: 3, cadence_days: 14}\n"
        "alerts: {drop_threshold_pct: 15, baseline_window_days: 30, min_observations: 4}\n"
    )
    with pytest.raises(ConfigError, match="IATA"):
        load_route(p)


def test_rejects_stay_inversion(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "route:\n  name: x\n  origins: [MAD]\n  destinations: [NBO]\n"
        "search_window:\n  earliest_departure: 2026-06-01\n  latest_return: 2027-05-31\n"
        "stay_preferences: {min_days: 60, max_days: 30}\n"
        "currency: EUR\n"
        "sweep: {outbound_window_days: 14, return_window_days: 14, overlap_days: 3, cadence_days: 14}\n"
        "alerts: {drop_threshold_pct: 15, baseline_window_days: 30, min_observations: 4}\n"
    )
    with pytest.raises(ConfigError, match="max_days"):
        load_route(p)


def test_one_way_config_roundtrip():
    """One-way config parses (no stay_preferences required) and round-trips
    through to_json; to_json emits trip_type for one_way."""
    from lib.config import route_from_json
    import json
    ow = {
        "trip_type": "one_way",
        "route": {"name": "ow1", "origins": ["MAD"], "destinations": ["BKK"]},
        "search_window": {"earliest_departure": "2026-10-01",
                          "latest_return": "2026-12-15"},
        "currency": "EUR",
        "sweep": {"cadence_days": 3},
        "alerts": {"drop_threshold_pct": 15, "baseline_window_days": 30,
                   "min_observations": 4},
    }
    cfg = route_from_json(json.dumps(ow))
    assert cfg.is_one_way
    assert cfg.stay.min_days == 0 and cfg.stay.max_days == 0
    assert route_from_json(cfg.to_json()) == cfg
    assert '"trip_type"' in cfg.to_json()


def test_round_trip_config_omits_trip_type_field():
    """B2: a round-trip config's serialized shape has NO trip_type key, so
    it is byte-identical to pre-one-way code (owner's search untouched)."""
    from lib.config import (RouteConfig, SearchWindow, StayPreferences,
                            SweepParams, FollowupParams, AlertParams,
                            route_to_yaml_dict)
    rt = RouteConfig(
        name="rt", origins=("MAD",), destinations=("NBO",),
        search_window=SearchWindow(date(2026, 9, 1), date(2027, 1, 15)),
        stay=StayPreferences(60, 90), currency="EUR",
        sweep=SweepParams(cadence_days=3), followup=FollowupParams(),
        alerts=AlertParams(15, 30, 4))
    assert "trip_type" not in route_to_yaml_dict(rt)
    assert '"trip_type"' not in rt.to_json()


def test_one_way_config_rejects_bad_trip_type():
    from lib.config import route_from_json, ConfigError
    import json
    bad = {"trip_type": "multi_city",
           "route": {"name": "x", "origins": ["MAD"], "destinations": ["BKK"]},
           "search_window": {"earliest_departure": "2026-10-01",
                             "latest_return": "2026-12-15"},
           "currency": "EUR", "sweep": {"cadence_days": 3},
           "alerts": {"drop_threshold_pct": 15, "baseline_window_days": 30,
                      "min_observations": 4}}
    try:
        route_from_json(json.dumps(bad))
        assert False, "should reject"
    except ConfigError:
        pass
