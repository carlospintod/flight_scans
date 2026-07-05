"""Tests for save_route write-back and legacy-key tolerance."""

from datetime import date
from pathlib import Path

import pytest
import yaml

from lib.config import (
    ConfigError, load_route, route_to_yaml_dict, save_route,
)


REPO = Path(__file__).resolve().parents[1]


def test_save_route_roundtrip_equals_loaded(tmp_path):
    """save_route(load_route(x)) preserves the meaningful config."""
    src = load_route(REPO / "routes" / "spain-nairobi.yaml")
    out = tmp_path / "rt.yaml"
    save_route(out, src)
    back = load_route(out)

    assert back.name == src.name
    assert back.origins == src.origins
    assert back.destinations == src.destinations
    assert back.search_window == src.search_window
    assert back.stay == src.stay
    assert back.currency == src.currency
    assert back.followup == src.followup
    assert back.alerts == src.alerts
    assert back.sweep.cadence_days == src.sweep.cadence_days
    assert back.sweep.skip_if_min_above == src.sweep.skip_if_min_above


def test_save_route_omits_legacy_window_keys(tmp_path):
    src = load_route(REPO / "routes" / "spain-nairobi.yaml")
    out = tmp_path / "rt.yaml"
    save_route(out, src)
    raw = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert "outbound_window_days" not in raw["sweep"]
    assert "return_window_days" not in raw["sweep"]
    assert "overlap_days" not in raw["sweep"]
    # But cadence + smart-skip survive.
    assert raw["sweep"]["cadence_days"] == 3
    assert raw["sweep"]["skip_if_min_above"] == 800


def test_save_route_validates_before_overwrite(tmp_path):
    """A config that fails validation must not touch the target file."""
    src = load_route(REPO / "routes" / "spain-nairobi.yaml")
    out = tmp_path / "rt.yaml"
    out.write_text("SENTINEL", encoding="utf-8")
    # Build an invalid route via route_to_yaml_dict then corrupt it — but
    # save_route takes a RouteConfig; simulate an invalid one by dataclass
    # replace on the frozen search_window to make latest <= earliest.
    from dataclasses import replace
    bad_sw = replace(src.search_window, latest_return=date(2020, 1, 1))
    bad = replace(src, search_window=bad_sw)
    with pytest.raises(ConfigError):
        save_route(out, bad)
    # Target file untouched.
    assert out.read_text(encoding="utf-8") == "SENTINEL"


def test_legacy_sweep_keys_accepted_and_ignored(tmp_path):
    """A YAML with the old window-size keys still loads without error."""
    p = tmp_path / "legacy.yaml"
    p.write_text(
        "route:\n  name: x\n  origins: [MAD]\n  destinations: [NBO]\n"
        "search_window:\n  earliest_departure: 2026-09-01\n  latest_return: 2027-05-31\n"
        "stay_preferences: {min_days: 60, max_days: 90}\n"
        "currency: EUR\n"
        # Old-style oversized 20x14 window that used to be REJECTED.
        "sweep: {outbound_window_days: 20, return_window_days: 14, overlap_days: 3, cadence_days: 14}\n"
        "alerts: {drop_threshold_pct: 15, baseline_window_days: 30, min_observations: 4}\n",
        encoding="utf-8",
    )
    r = load_route(p)  # must NOT raise (validation removed)
    assert r.stay.min_days == 60
    assert r.sweep.cadence_days == 14


def test_route_to_yaml_dict_shape():
    src = load_route(REPO / "routes" / "spain-nairobi.yaml")
    d = route_to_yaml_dict(src)
    assert d["route"]["name"] == "spain-nairobi"
    assert d["search_window"]["earliest_departure"] == "2026-09-12"
    assert d["stay_preferences"]["max_days"] == 90
    assert "followup" in d  # spain-nairobi has followup thresholds
