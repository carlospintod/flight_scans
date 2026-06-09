"""Defensive-parser tests for Aviasales.

Real response shape will be verified against `probe_new_sources.py`
output once Carlos has the token. Until then these tests pin the
parser's behavior on a representative payload synthesized from the
Travelpayouts docs.
"""

from lib.aviasales_api import _parse_quotes


def test_parses_v3_prices_for_dates_shape():
    """The shape Travelpayouts returns from /aviasales/v3/prices_for_dates."""
    payload = {
        "success": True,
        "data": [
            {
                "origin": "MAD",
                "destination": "NBO",
                "origin_airport": "MAD",
                "destination_airport": "NBO",
                "price": 412,
                "airline": "SV",   # Saudia — the gap we built this for
                "flight_number": "212",
                "departure_at": "2026-10-15T14:30:00+02:00",
                "return_at": "2026-12-10T09:55:00+03:00",
                "transfers": 1,
                "return_transfers": 1,
                "duration": 1380,
                "found_at": "2026-06-09T12:34:56Z",
                "expires_at": "2026-06-10T12:34:56Z",
            },
            {
                "origin": "MAD",
                "destination": "NBO",
                "price": 487,
                "airline": "QR",
                "departure_at": "2026-10-16T22:15:00+02:00",
                "return_at": "2026-12-11T05:25:00+03:00",
            },
        ],
    }
    quotes = _parse_quotes(payload, "EUR")
    assert len(quotes) == 2
    assert quotes[0].airline == "SV"
    assert quotes[0].price == 412
    assert quotes[0].departure_date == "2026-10-15"
    assert quotes[0].return_date == "2026-12-10"
    assert quotes[0].currency == "EUR"


def test_parses_v1_cheap_prices_shape_with_yyyy_mm_dd_keys():
    """/v1/prices/cheap uses depart_date/return_date directly."""
    payload = {
        "success": True,
        "data": [
            {
                "origin": "MAD",
                "destination": "NBO",
                "depart_date": "2026-10-15",
                "return_date": "2026-12-10",
                "price": 380,
                "airline": "SV",
                "flight_number": "212",
            },
        ],
    }
    quotes = _parse_quotes(payload, "EUR")
    assert len(quotes) == 1
    assert quotes[0].departure_date == "2026-10-15"
    assert quotes[0].return_date == "2026-12-10"
    assert quotes[0].airline == "SV"


def test_skips_one_way_and_zero_price():
    payload = {
        "data": [
            # one-way (no return_at, no return_date) — kept; return_date is None
            {"origin": "MAD", "destination": "NBO",
             "departure_at": "2026-10-15T00:00:00Z", "price": 199, "airline": "VY"},
            # zero price — dropped
            {"origin": "MAD", "destination": "NBO",
             "depart_date": "2026-10-15", "return_date": "2026-12-10",
             "price": 0, "airline": "??"},
            # missing origin — dropped
            {"destination": "NBO", "depart_date": "2026-10-15",
             "price": 412, "airline": "SV"},
        ],
    }
    quotes = _parse_quotes(payload, "EUR")
    assert len(quotes) == 1
    assert quotes[0].return_date is None
    assert quotes[0].airline == "VY"


def test_handles_data_missing_or_non_list():
    assert _parse_quotes({}, "EUR") == []
    assert _parse_quotes({"data": None}, "EUR") == []
    assert _parse_quotes({"data": "not a list"}, "EUR") == []
