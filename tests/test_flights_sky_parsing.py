"""flights-sky (Skyscanner-proxy) parser — proven against a REAL sample
response (tests/fixtures/flights_sky_detail.json, captured 2026-07-13).
The OTA-coverage payload is pricingOptions[].agents[].{name,price}."""

import json
from pathlib import Path

from lib.flights_sky import ScannerOption, parse_itineraries

FIXTURE = Path(__file__).parent / "fixtures" / "flights_sky_detail.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parses_cheapest_ota_seller_from_real_response():
    opts = parse_itineraries(_payload(), currency="USD")
    assert len(opts) == 1
    o = opts[0]
    assert isinstance(o, ScannerOption)
    # The cheapest booking option is the OTA (Kiwi.com @ 598), BELOW the
    # airline-direct price (French Bee @ 645) — exactly the OTA discount
    # Google-based sources under-report.
    assert o.price == 598
    assert o.seller == "Kiwi.com"
    assert o.seller_is_ota is True
    # ...and it's a self-transfer combo (French Bee + Spirit), the Kiwi
    # virtual-interlining coverage the audit said we'd lose.
    assert o.is_self_transfer is True
    assert o.carriers == "French Bee + Spirit Airlines"
    assert o.stops == 1
    assert o.total_minutes == 760        # real leg duration (incl. layover)
    assert o.departure_date == "2024-02-15"
    assert o.return_date == ""           # single leg -> one-way


def test_airline_direct_would_win_when_cheaper():
    payload = _payload()
    # Flip prices: airline-direct becomes cheapest.
    for opt in payload["data"]["itinerary"]["pricingOptions"]:
        if opt["id"] == "airlineDirect":
            opt["totalPrice"] = 400
            opt["agents"][0]["price"] = 400
    o = parse_itineraries(payload, currency="USD")[0]
    assert o.price == 400
    assert o.seller == "French Bee"
    assert o.seller_is_ota is False       # airline, not an OTA


def test_empty_response_parses_to_nothing():
    assert parse_itineraries({"data": {}, "status": True}, "USD") == []
    assert parse_itineraries({}, "USD") == []
