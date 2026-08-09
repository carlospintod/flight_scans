"""Google Travel Explore — the long-haul discovery rail (D1 amendment).

Fixtures are real responses captured 2026-08-09 from the live engine.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from lib import explore_api
from lib.dealconfig import load_deal_config

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = load_deal_config()


def _fx(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parses_a_real_area_response():
    quotes = explore_api.parse_explore(_fx("serpapi_explore_area.json"),
                                       origin="MAD")
    assert quotes
    by_dest = {q.dest: q for q in quotes}
    # The measured long-haul that the cached sweep could never verify.
    assert "EWR" in by_dest
    ewr = by_dest["EWR"]
    assert ewr.origin == "MAD"
    assert ewr.price == 398
    assert ewr.depart_date == "2026-11-02"
    assert ewr.return_date == "2026-11-08"
    assert ewr.airline == "Tap Air Portugal"


def test_rows_without_a_price_are_dropped_not_guessed():
    """Measured: 21 of 66 rows carry no price, airport code or dates —
    Google renders those cards without a fare. A row without a price is
    not an observation."""
    raw = _fx("serpapi_explore_area.json")
    total = len(raw["destinations"])
    quotes = explore_api.parse_explore(raw, origin="MAD")
    assert 0 < len(quotes) < total
    assert all(q.price > 0 and q.dest and q.depart_date for q in quotes)


def test_garbage_rows_never_crash_the_parser():
    payload = {"destinations": [
        None, "nonsense", {}, {"flight_price": 100},          # no airport
        {"destination_airport": {"code": "JFK"}},              # no price
        {"destination_airport": {"code": "JFK"}, "flight_price": "cheap",
         "start_date": "2026-11-02"},                          # price not int
        {"destination_airport": {"code": "jfk"}, "flight_price": 400,
         "start_date": "2026-11-02"},                          # the only good one
    ]}
    quotes = explore_api.parse_explore(payload, origin="mad")
    assert len(quotes) == 1
    assert quotes[0].dest == "JFK" and quotes[0].origin == "MAD"
    assert quotes[0].return_date is None


def test_explore_gives_airport_codes_not_metro_codes():
    """The metro-code bug's root cause was Aviasales naming cities.
    Explore names airports, so these candidates verify as-is."""
    quotes = explore_api.parse_explore(_fx("serpapi_explore_area.json"),
                                       origin="MAD")
    metros = set(CONFIG.metro_airports)          # NYC, LON, TYO, ...
    assert not ({q.dest for q in quotes} & metros)


def test_observations_are_nominations_not_verifications():
    """Explore is Google-family and live-ish, but the "no alert without
    live verification" rule must be untouched."""
    quotes = explore_api.parse_explore(_fx("serpapi_explore_area.json"),
                                       origin="MAD")
    obs = explore_api.to_observations(quotes)
    assert obs and all(o.is_verified is False for o in obs)
    assert {o.source for o in obs} == {"explore"}
    # Same family as the verification, so confidence cannot count them
    # as two independent views of the market.
    assert {o.source_family for o in obs} == {"google"}
    from lib.confidence import ConfidenceResult  # noqa: F401
    from lib.sources import families_of
    assert families_of(["serpapi_vz", "googleflights_vz"]) == {"google"}


# -- the rotation ------------------------------------------------------

MONTHS = ["2026-09", "2026-10", "2026-11", "2026-12", "2027-01", "2027-02"]
ORIGINS = ["MAD", "BCN", "VLC", "ALC"]
AREAS = ["north_america", "south_america", "asia", "africa", "oceania"]


def test_rotation_is_deterministic_per_day():
    a = explore_api.rotation_plan(ORIGINS, AREAS, MONTHS,
                                  day=date(2026, 8, 12), budget=4)
    b = explore_api.rotation_plan(ORIGINS, AREAS, MONTHS,
                                  day=date(2026, 8, 12), budget=4)
    assert a == b, "a re-run on the same day must not drift and double-spend"
    assert len(a) == 4


def test_rotation_walks_the_whole_grid_without_starving_a_window():
    grid_size = len(ORIGINS) * len(AREAS) * len(MONTHS)
    seen = set()
    start = date(2026, 8, 10)
    for i in range(grid_size // 4):
        seen.update(explore_api.rotation_plan(
            ORIGINS, AREAS, MONTHS,
            day=date.fromordinal(start.toordinal() + i), budget=4))
    assert len(seen) == grid_size, f"only {len(seen)} of {grid_size} covered"


def test_rotation_handles_a_budget_larger_than_the_grid():
    plan = explore_api.rotation_plan(["MAD"], ["asia"], ["2026-09"],
                                     day=date(2026, 8, 12), budget=50)
    assert len(plan) == 1


def test_rotation_is_empty_without_budget_or_grid():
    assert explore_api.rotation_plan(ORIGINS, AREAS, MONTHS,
                                     day=date(2026, 8, 12), budget=0) == []
    assert explore_api.rotation_plan([], AREAS, MONTHS,
                                     day=date(2026, 8, 12), budget=4) == []


# -- vendor portability + config ---------------------------------------

def test_europe_is_deliberately_not_an_explore_area():
    """The free cached sweep already covers intra-EU breadth at zero
    quota — paying a Google call for it buys nothing."""
    assert "europe" not in explore_api.AREAS
    assert "europe" not in CONFIG.explore_areas
    assert set(CONFIG.explore_areas) <= set(explore_api.AREAS)


def test_both_vendors_are_wired_for_the_same_engine():
    """The SearchAPI flip must be a config change, not a rewrite."""
    assert set(explore_api.PROVIDERS) == {"serpapi", "searchapi"}
    assert CONFIG.explore_provider in explore_api.PROVIDERS


def test_an_unknown_provider_or_area_is_refused_loudly():
    with pytest.raises(explore_api.ExploreError, match="provider"):
        explore_api.ExploreClient("k", provider="bing")
    client = explore_api.ExploreClient("k", provider="serpapi")
    with pytest.raises(explore_api.ExploreError, match="area"):
        client.explore(origin="MAD", month="2026-11", area="atlantis")


def test_explore_is_metered_on_both_vz_pools():
    """Non-negotiable #1: no source is called without a ledger spec."""
    from lib.quota import METERED
    assert METERED["serpapi_vz"]["explore"] == 1
    assert METERED["searchapi_vz"]["explore"] == 1


def test_run_deals_splits_the_shared_serpapi_budget():
    """Two GuardedClients on one source would each be handed the full
    reservation and could together exceed it."""
    src = (Path(__file__).resolve().parents[1] / "run_deals.py").read_text(
        encoding="utf-8")
    assert "budget_units=n_cand" in src
    assert "budget_units=len(explore_windows)" in src
