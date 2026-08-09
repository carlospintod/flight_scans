"""Origin-only anywhere sweep: request shape + defensive parsing.

Fixture transcribed from the documented /aviasales/v3/prices_for_dates
response shape (list items carry their own destination when the request
omits it), with VLC-realistic values. Replace with a captured live
response after the first real sweep if the shape drifts.
"""

import json
from pathlib import Path

import pytest

from lib.aviasales_api import AviasalesClient

FIXTURE = Path(__file__).parent / "fixtures" / "aviasales_anywhere.json"


class _Resp:
    ok = True
    status_code = 200
    headers: dict = {}

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self.payload = payload
        self.seen_params: dict = {}
        self.seen_url = ""

    def get(self, url, params=None, headers=None, timeout=None):
        self.seen_url = url
        self.seen_params = dict(params or {})
        return _Resp(self.payload)


@pytest.fixture()
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_anywhere_omits_destination_and_parses_all_routes(payload):
    session = _Session(payload)
    client = AviasalesClient(token="t", session=session)
    resp = client.anywhere_prices(origin="VLC", month="2026-09")
    assert "destination" not in session.seen_params
    assert session.seen_params["origin"] == "VLC"
    assert session.seen_params["departure_at"] == "2026-09"
    dests = {q.destination for q in resp.quotes}
    assert {"MRS", "FCO", "NBO", "JFK"} <= dests
    mrs = next(q for q in resp.quotes if q.destination == "MRS")
    assert mrs.price == 38 and mrs.origin == "VLC"
    assert mrs.departure_date == "2026-09-10"
    assert mrs.return_date == "2026-09-14"
    assert mrs.found_at == "2026-08-07T18:20:11Z"


def test_anywhere_without_month_omits_departure_at(payload):
    session = _Session(payload)
    client = AviasalesClient(token="t", session=session)
    client.anywhere_prices(origin="VLC")
    assert "departure_at" not in session.seen_params


def test_anywhere_sorting_and_limit_are_caller_controlled(payload):
    """price-sorted returns the cheapest N (intra-EU heavy); route-sorted
    returns breadth (where long-haul lives). Both are free."""
    session = _Session(payload)
    client = AviasalesClient(token="t", session=session)
    client.anywhere_prices(origin="BCN", month="2026-11")
    assert session.seen_params["sorting"] == "price"
    assert session.seen_params["limit"] == 100
    client.anywhere_prices(origin="BCN", month="2026-11",
                           sorting="route", limit=1000)
    assert session.seen_params["sorting"] == "route"
    assert session.seen_params["limit"] == 1000


def test_same_day_round_trip_is_dropped_to_one_way():
    """/v2/prices/latest echoes return_at == departure_at; passing that
    downstream asked Google for a same-day round trip and returned
    nothing — it killed every long-haul verification."""
    from lib.aviasales_api import _parse_quotes
    payload = {"data": [
        {"origin": "MAD", "destination": "NYC", "price": 549,
         "departure_at": "2026-10-16T10:00:00Z",
         "return_at": "2026-10-16T22:00:00Z"},
        {"origin": "MAD", "destination": "MIA", "price": 620,
         "departure_at": "2026-10-16T10:00:00Z",
         "return_at": "2026-10-30T22:00:00Z"},
    ]}
    quotes = _parse_quotes(payload, "eur", origin_default="MAD")
    nyc = next(q for q in quotes if q.destination == "NYC")
    mia = next(q for q in quotes if q.destination == "MIA")
    assert nyc.return_date is None          # honest one-way, not a fake pair
    assert mia.return_date == "2026-10-30"  # a real pair survives


def test_prices_for_dates_accepts_a_month(payload):
    session = _Session(payload)
    client = AviasalesClient(token="t", session=session)
    client.prices_for_dates(origin="MAD", destination="NYC",
                            depart_month="2026-11")
    assert session.seen_params["departure_at"] == "2026-11"
    assert session.seen_params["destination"] == "NYC"


def test_every_aviasales_price_method_is_metered():
    """Introspection pin (the kiwi lesson, 2026-07-08): any public
    *_prices method that bypasses METERED would spend unguarded."""
    from lib.sources import METERED
    metered = set(METERED["aviasales"])
    methods = {name for name in dir(AviasalesClient)
               if name.endswith("_prices") and not name.startswith("_")}
    assert methods <= metered, f"unmetered aviasales methods: {methods - metered}"
