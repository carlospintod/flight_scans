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
    assert cfg.sweep.outbound_window_days == 14
    assert cfg.sweep.return_window_days == 14
    assert cfg.sweep.overlap_days == 3
    assert cfg.sweep.cadence_days == 14
    assert cfg.sweep.skip_if_min_above == 800
    assert cfg.sweep.skip_grace_days == 60
    assert cfg.followup.watch_below_price == 600
    assert cfg.followup.drop_above_price == 800
    assert cfg.alerts.drop_threshold_pct == 15
    assert cfg.alerts.baseline_window_days == 30
    assert cfg.alerts.min_observations == 4
    # search_window dates are dates, not strings
    assert cfg.search_window.earliest_departure.year == 2026
    assert cfg.search_window.earliest_departure.month == 9
    assert cfg.search_window.latest_return.year == 2027


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
