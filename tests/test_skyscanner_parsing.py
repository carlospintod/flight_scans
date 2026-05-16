"""Tests for the Sky Scrapper response parsers.

Driven by captured probe responses in tests/fixtures/skyscanner_*.json.
These run with no network access.
"""

import json
from pathlib import Path

import pytest

from lib.skyscanner_rapidapi import (
    SkyScrapperError,
    _parse_curve,
    _parse_flights,
    _pick_airport,
)

FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_pick_airport_prefers_airport_typed_skyid_match():
    """MAD searchAirport response lists Madurai first; the picker should
    skip it and return Madrid's record."""
    payload = _load("skyscanner_airport_mad.json")
    sky, ent, name = _pick_airport(payload, "MAD")
    assert sky == "MAD"
    assert ent == "95565077"
    assert name and "Madrid" in name


def test_pick_airport_raises_on_no_match():
    with pytest.raises(SkyScrapperError):
        _pick_airport({"data": []}, "XXX")


def test_curve_parser_skips_invalid_entries():
    payload = _load("skyscanner_calendar.json")
    entries = _parse_curve(payload)
    assert len(entries) >= 200, "expected ~206 days in the curve"
    # No zero or negative prices.
    assert all(e.price > 0 for e in entries)
    # All day strings look like YYYY-MM-DD.
    for e in entries[:10]:
        assert len(e.departure_date) == 10
        assert e.departure_date.count("-") == 2
        assert e.price_group in {"low", "medium", "high", None}


def test_curve_parser_handles_empty():
    assert _parse_curve({}) == []
    assert _parse_curve({"data": {"flights": {"days": []}}}) == []


def test_flights_parser_returns_sorted_options_with_self_transfer_flag():
    payload = _load("skyscanner_flights.json")
    flights = _parse_flights(payload)
    assert flights, "expected at least one itinerary in the fixture"
    # Prices sorted ascending.
    prices = [f.price for f in flights]
    assert prices == sorted(prices)
    # Carriers field is human-readable.
    assert all(f.carriers and f.carriers != "unknown" for f in flights)
    # is_self_transfer is a bool.
    assert all(isinstance(f.is_self_transfer, bool) for f in flights)


def test_flights_parser_aggregates_carriers_across_legs():
    """The probe fixture shows 'KLM + Kenya Airways' style multi-leg
    bundles. Ensure the parser deduplicates and joins with ' + '."""
    payload = _load("skyscanner_flights.json")
    flights = _parse_flights(payload)
    # At least one option should be multi-carrier or single-carrier;
    # both are acceptable. Just check the format.
    for f in flights:
        # No duplicate carrier names.
        parts = f.carriers.split(" + ")
        assert len(parts) == len(set(parts))
