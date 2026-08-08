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


def test_every_aviasales_price_method_is_metered():
    """Introspection pin (the kiwi lesson, 2026-07-08): any public
    *_prices method that bypasses METERED would spend unguarded."""
    from lib.sources import METERED
    metered = set(METERED["aviasales"])
    methods = {name for name in dir(AviasalesClient)
               if name.endswith("_prices") and not name.startswith("_")}
    assert methods <= metered, f"unmetered aviasales methods: {methods - metered}"
