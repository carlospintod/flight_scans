"""Parser tests for the Kiwi RapidAPI adapter.

Driven by the actual response shape captured via probe_new_sources.py
on 2026-06-10. The full real response is large (153 KB) so these tests
use trimmed but structurally accurate fixtures.
"""

from lib.kiwi_rapidapi import _carriers_from_segments, _parse_options


def _seg(carrier_code: str, src_code: str, dst_code: str,
         src_time: str = "2026-10-15T12:30:00",
         dst_time: str = "2026-10-15T15:00:00") -> dict:
    """Build a sectorSegments[] entry matching the real shape."""
    return {
        "guarantee": "STANDARD",
        "segment": {
            "id": "seg-id",
            "source": {"localTime": src_time, "station": {"code": src_code}},
            "destination": {"localTime": dst_time, "station": {"code": dst_code}},
            "duration": 9000,
            "type": "FLIGHT",
            "code": f"{carrier_code}999",
            "carrier": {"code": carrier_code, "name": f"{carrier_code} Airlines"},
            "operatingCarrier": {"code": carrier_code},
            "cabinClass": "ECONOMY",
        },
        "layover": None,
    }


def test_parses_real_kiwi_shape_with_virtual_interlining():
    payload = {
        "__typename": "Itineraries",
        "metadata": {"itinerariesCount": 1},
        "itineraries": [
            {
                "__typename": "ItineraryReturn",
                "id": "abc",
                "price": {"amount": "1101", "priceBeforeDiscount": "1101"},
                "priceEur": {"amount": "1101"},
                "travelHack": {
                    "isVirtualInterlining": True,
                    "isTrueHiddenCity": False,
                    "isThrowawayTicket": False,
                },
                "outbound": {
                    "id": "ob",
                    "duration": 84900,  # seconds → 1415 minutes
                    "sectorSegments": [
                        _seg("FR", "MAD", "DOH",
                             "2026-10-15T12:30:00", "2026-10-15T18:00:00"),
                        _seg("KQ", "DOH", "NBO",
                             "2026-10-15T22:00:00", "2026-10-16T05:30:00"),
                    ],
                },
                "inbound": {
                    "id": "ib",
                    "duration": 50000,
                    "sectorSegments": [
                        _seg("SV", "NBO", "JED",
                             "2026-12-10T09:55:00", "2026-12-10T15:30:00"),
                        _seg("SV", "JED", "MAD",
                             "2026-12-10T17:00:00", "2026-12-10T22:00:00"),
                    ],
                },
            },
        ],
    }
    opts = _parse_options(payload, "EUR")
    assert len(opts) == 1
    o = opts[0]
    assert o.price == 1101
    assert o.fly_from == "MAD"
    assert o.fly_to == "NBO"
    assert o.depart_date == "2026-10-15"
    assert o.return_date == "2026-12-10"
    assert o.carriers == "FR + KQ + SV"
    assert o.is_virtual_interlining is True
    # 2 outbound segments => 1 stop; 2 inbound => 1 stop; total 2.
    assert o.stops == 2
    # (84900 + 50000) / 60 = 2248 minutes.
    assert o.total_minutes == (84900 + 50000) // 60


def test_sorts_options_ascending_by_price():
    payload = {
        "itineraries": [
            {
                "price": {"amount": "489"},
                "priceEur": {"amount": "489"},
                "travelHack": {"isVirtualInterlining": False},
                "outbound": {"duration": 720 * 60,
                             "sectorSegments": [_seg("EY", "MAD", "NBO")]},
                "inbound": {"duration": 720 * 60,
                            "sectorSegments": [_seg("EY", "NBO", "MAD")]},
            },
            {
                "price": {"amount": "245"},
                "priceEur": {"amount": "245"},
                "travelHack": {"isVirtualInterlining": True},
                "outbound": {"duration": 1380 * 60,
                             "sectorSegments": [_seg("FR", "MAD", "DOH"),
                                                _seg("KQ", "DOH", "NBO")]},
                "inbound": {"duration": 1380 * 60,
                            "sectorSegments": [_seg("KQ", "NBO", "MAD")]},
            },
        ],
    }
    opts = _parse_options(payload, "EUR")
    assert [o.price for o in opts] == [245, 489]
    assert opts[0].is_virtual_interlining is True


def test_drops_itineraries_missing_required_fields():
    payload = {
        "itineraries": [
            # No outbound segments — dropped.
            {"price": {"amount": "300"},
             "outbound": {"sectorSegments": []}, "inbound": {}},
            # No price — dropped.
            {"outbound": {"sectorSegments": [_seg("EY", "MAD", "NBO")]},
             "inbound": {"sectorSegments": [_seg("EY", "NBO", "MAD")]}},
            # Zero price — dropped.
            {"price": {"amount": "0"},
             "outbound": {"sectorSegments": [_seg("EY", "MAD", "NBO")]}},
        ],
    }
    assert _parse_options(payload, "EUR") == []


def test_handles_empty_or_malformed_payload():
    assert _parse_options({}, "EUR") == []
    assert _parse_options({"itineraries": None}, "EUR") == []
    assert _parse_options({"itineraries": "not a list"}, "EUR") == []


def test_carriers_helper_dedupes_across_legs():
    segs = [
        _seg("FR", "MAD", "DOH"),
        _seg("FR", "DOH", "AUH"),   # same carrier on next outbound segment
        _seg("KQ", "AUH", "NBO"),
        _seg("KQ", "NBO", "MAD"),   # return uses one of the same carriers
    ]
    assert _carriers_from_segments(segs) == "FR + KQ"