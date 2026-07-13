"""The source registry (R4) — one declarative object per source.

The golden test pins the derived POOL_SEEDS/METERED byte-identical to
the literals they replaced, so the refactor changed nothing at runtime.
"""

from lib import sources
from lib.quota import METERED, POOL_SEEDS


# The exact literals that lived in lib/quota.py before the registry.
_GOLDEN_POOL_SEEDS = (
    ("kiwi", "monthly", 300, 10, 15, 10, None),
    ("serpapi", "monthly", 250, None, 25, 7, None),
    ("aviasales", "rate_only", None, None, 0, None, None),
    ("googleflights", "per_run", None, None, 0, 25, 30),
)
_GOLDEN_METERED = {
    "kiwi": {"range_search": 1, "round_trip_search": 1, "one_way_search": 1,
             "one_way_range_search": 1},
    "serpapi": {"point_query": 1, "booking_options": 1},
    "flights_sky": {"search_roundtrip": 1, "search_one_way": 1,
                    "flight_details": 1, "price_calendar": 1},
    "searchapi": {"point_query": 1, "calendar": 1},
    "googleflights": {"point_query": 1},
    "aviasales": {"cheap_prices": 1, "prices_for_dates": 1,
                  "latest_prices": 1, "one_way_month_prices": 1},
    "skyscanner": {"point_query": 2, "search_airport": 1},
}


def test_derived_pool_seeds_are_byte_identical():
    assert POOL_SEEDS == _GOLDEN_POOL_SEEDS
    assert sources.POOL_SEEDS == _GOLDEN_POOL_SEEDS


def test_derived_metered_is_byte_identical():
    assert METERED == _GOLDEN_METERED


def test_families_are_correctly_grouped():
    # The audit's key rule: Google endpoints are ONE family, Skyscanner
    # proxies are ONE family — so two Google mirrors can't inflate
    # confidence.
    assert sources.family_of("serpapi") == sources.family_of("googleflights") \
        == sources.family_of("searchapi") == "google"
    assert sources.family_of("flights_sky") == sources.family_of("skyscanner") \
        == "ota_metasearch"
    assert sources.family_of("aviasales") == "cached"
    # Three google sources = ONE family; add an OTA source = TWO.
    assert sources.families_of(["serpapi", "googleflights"]) == {"google"}
    assert sources.families_of(["serpapi", "flights_sky", "aviasales"]) \
        == {"google", "ota_metasearch", "cached"}


def test_role_map_defaults_to_enabled_only():
    rm = sources.role_map()
    # kiwi/flights_sky/skyscanner/searchapi are enabled=False -> excluded.
    assert "kiwi" not in rm.get("discovery", [])
    assert "aviasales" in rm.get("discovery", [])
    assert "serpapi" in rm.get("verification", [])
    # Explicit source list is honoured verbatim.
    rm2 = sources.role_map(["aviasales", "serpapi"])
    assert rm2 == {"discovery": ["aviasales"], "corroboration": ["aviasales"],
                   "verification": ["serpapi"]}


def test_every_pooled_source_has_metered_methods():
    for s in sources.REGISTRY:
        if s.pool is not None:
            assert s.metered, f"{s.id} has a pool but no metered methods"
