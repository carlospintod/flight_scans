"""Best-price confidence (R5) — counts INDEPENDENT coverage families,
not endpoints, per the 2026-07-13 audit."""

from lib.confidence import (
    ConfidenceResult,
    assess_confidence,
    confidence_drop_push,
)
from lib.health import SourceHealth


def _live(source: str) -> SourceHealth:
    return SourceHealth(source=source, verdict="live", attempts=3, ok=3, stored=20)


def _dark(source: str) -> SourceHealth:
    return SourceHealth(source=source, verdict="dark", attempts=3, ok=0, stored=0)


def test_two_google_mirrors_are_one_family_medium():
    """serpapi + googleflights both live is still ONE family (Google) —
    must NOT read as two independent confirmations."""
    r = assess_confidence({"serpapi": _live("serpapi"),
                           "googleflights": _live("googleflights")})
    assert r.level == "medium"
    assert r.families == ["google"]


def test_google_plus_ota_is_high():
    r = assess_confidence({"googleflights": _live("googleflights"),
                           "flights_sky": _live("flights_sky"),
                           "aviasales": _live("aviasales")})
    assert r.level == "high"
    assert set(r.families) == {"google", "ota_metasearch", "cached"}
    assert "OTA prices are leads" in r.note      # net-price honesty


def test_cached_only_is_low():
    """Only Travelpayouts producing (live verification dark) -> low."""
    r = assess_confidence({"aviasales": _live("aviasales"),
                           "googleflights": _dark("googleflights"),
                           "serpapi": _dark("serpapi")})
    assert r.level == "low"
    assert "cached-only" in r.note


def test_nothing_producing_is_no_data():
    r = assess_confidence({"googleflights": _dark("googleflights")})
    assert r.level == "no_data"


def test_confidence_drop_pages_on_worsening_only():
    high = ConfidenceResult("high", 97, ["google", "ota_metasearch"], True)
    low = ConfidenceResult("low", 72, ["cached"], False, note="cached-only")
    # high -> low pages...
    push = confidence_drop_push(low, high)
    assert push and "dropped to low" in push["title"]
    # ...low -> low does not (no worsening)...
    assert confidence_drop_push(low, low) is None
    # ...and recovering low -> high does not page.
    assert confidence_drop_push(high, low) is None
