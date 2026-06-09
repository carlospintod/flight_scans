"""Defensive-parser tests for the Kiwi RapidAPI adapter.

emir12's response shape isn't fully documented, so the parser tries
a few key variants. These tests pin behavior against representative
synthesized payloads — we'll tighten them against real responses once
Carlos has subscribed to the RapidAPI listing.
"""

from lib.kiwi_rapidapi import _carriers_from_kiwi, _parse_options


def test_parses_typical_kiwi_itineraries():
    payload = {
        "itineraries": [
            {
                "id": "abc",
                "price": 245,
                "flyFrom": "MAD",
                "flyTo": "NBO",
                "local_departure": "2026-10-15T14:30:00.000Z",
                "local_arrival_inbound": "2026-12-10T09:55:00.000Z",
                "airlines": ["FR", "KQ"],   # Ryanair + Kenya Airways
                "route": [
                    {"airline": "FR", "flyFrom": "MAD", "flyTo": "DOH"},
                    {"airline": "KQ", "flyFrom": "DOH", "flyTo": "NBO"},
                ],
                "duration": {"total": 1380},
                "virtual_interlining": True,
            },
            {
                "id": "def",
                "price": 489,
                "flyFrom": "MAD",
                "flyTo": "NBO",
                "local_departure": "2026-10-16T22:15:00.000Z",
                "local_arrival_inbound": "2026-12-11T05:25:00.000Z",
                "airlines": ["EY"],
                "route": [{"airline": "EY", "flyFrom": "MAD", "flyTo": "NBO"}],
                "duration": {"total": 720},
                "virtual_interlining": False,
            },
        ],
    }
    opts = _parse_options(payload, "EUR")
    assert len(opts) == 2
    # Sorted by price ascending.
    assert opts[0].price == 245
    assert opts[1].price == 489
    # Virtual-interlining flag picked up.
    assert opts[0].is_virtual_interlining is True
    assert opts[1].is_virtual_interlining is False
    # Carriers joined with ' + ' for multi-carrier bundles.
    assert opts[0].carriers == "FR + KQ"
    assert opts[1].carriers == "EY"
    # Stops derived from route segments (n segments -> n-1 stops).
    assert opts[0].stops == 1
    assert opts[1].stops == 0


def test_parses_data_key_variant():
    """Some Kiwi listings put results under `data` instead of `itineraries`."""
    payload = {
        "data": [
            {
                "price": 320,
                "fly_from": "BCN",
                "fly_to": "NBO",
                "departure_date": "2026-11-01",
                "return_date": "2026-12-30",
                "airlines": ["TK"],
                "route": [{"airline": "TK"}],
                "duration": 900,
                "virtual_interlining": False,
            },
        ],
    }
    opts = _parse_options(payload, "EUR")
    assert len(opts) == 1
    assert opts[0].fly_from == "BCN"
    assert opts[0].depart_date == "2026-11-01"
    assert opts[0].total_minutes == 900


def test_drops_invalid_entries():
    payload = {
        "itineraries": [
            {"price": 0, "flyFrom": "MAD", "flyTo": "NBO",
             "local_departure": "2026-10-15"},  # zero price
            {"price": 300, "flyTo": "NBO",
             "local_departure": "2026-10-15"},  # missing origin
            {"price": 250, "flyFrom": "MAD", "flyTo": "NBO"},  # missing date
        ],
    }
    assert _parse_options(payload, "EUR") == []


def test_handles_empty_or_malformed_payload():
    assert _parse_options({}, "EUR") == []
    assert _parse_options({"itineraries": None}, "EUR") == []


def test_carriers_helper_dedupes():
    """Same carrier appearing in airlines AND route shouldn't double up."""
    itin = {
        "airlines": ["FR", "KQ"],
        "route": [{"airline": "FR"}, {"airline": "KQ"}, {"airline": "FR"}],
    }
    assert _carriers_from_kiwi(itin) == "FR + KQ"
