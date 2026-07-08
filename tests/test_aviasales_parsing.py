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


def test_parses_v1_cheap_nested_shape():
    """Real /v1/prices/cheap shape observed via probe 2026-06-10:

        {
          "data": {"NBO": {"1": {<quote>}, "2": {<quote>}, ...}},
          "currency": "eur",
          "success": true
        }
    """
    payload = {
        "success": True,
        "currency": "eur",
        "data": {
            "NBO": {
                "1": {
                    "airline": "EY",
                    "departure_at": "2026-09-07T10:45:00+02:00",
                    "return_at": "2026-09-19T19:00:00+03:00",
                    "expires_at": "2026-06-10T01:01:47Z",
                    "price": 558,
                    "flight_number": 102,
                    "duration": 3360,
                },
                "2": {
                    "airline": "SV",
                    "departure_at": "2026-10-15T14:30:00+02:00",
                    "return_at": "2026-12-10T09:55:00+03:00",
                    "price": 412,
                    "flight_number": 212,
                },
            },
        },
    }
    quotes = _parse_quotes(payload, "EUR", origin_default="MAD")
    assert len(quotes) == 2
    airlines = {q.airline for q in quotes}
    assert airlines == {"EY", "SV"}
    # Destination patched in from outer key; origin patched from request.
    assert all(q.destination == "NBO" for q in quotes)
    assert all(q.origin == "MAD" for q in quotes)


def test_empty_nested_shape_is_handled():
    payload = {"success": True, "currency": "eur", "data": {"NBO": {}}}
    assert _parse_quotes(payload, "EUR") == []


def test_one_way_month_prices_params_and_sentinel_parse():
    """/aviasales/v3/prices_for_dates one_way=true: month-granular
    departure_at, items without return_at parse to return_date=None
    (shape probed live 2026-07-08, MAD->NBO 2026-09)."""
    from lib.aviasales_api import AviasalesClient

    seen: dict = {}

    class _Resp:
        ok = True
        status_code = 200
        headers: dict = {}

        @staticmethod
        def json():
            return {"success": True, "data": [
                {"origin": "MAD", "destination": "NBO",
                 "departure_at": "2026-09-06T21:50:00+02:00",
                 "price": 260, "airline": "EY", "flight_number": "104"},
            ]}

    class _Session:
        def get(self, url, params=None, headers=None, timeout=None):
            seen["url"] = url
            seen.update(params or {})
            return _Resp()

    client = AviasalesClient(token="t", session=_Session())
    resp = client.one_way_month_prices(origin="MAD", destination="NBO",
                                       month="2026-09", currency="EUR")
    assert seen["url"].endswith("/aviasales/v3/prices_for_dates")
    assert seen["one_way"] == "true"
    assert seen["departure_at"] == "2026-09"
    q = resp.quotes[0]
    assert q.departure_date == "2026-09-06"
    assert q.return_date is None
    assert q.price == 260
