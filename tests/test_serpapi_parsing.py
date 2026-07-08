"""SerpAPI adapter: parser reuse, other_flights fallback, quota shape.

Fixture transcribed from SerpAPI's public google_flights documentation
example structure (serpapi.com/google-flights-results, checked
2026-07-06) with corridor-realistic values. Replace with a captured live
response after the first funded query (1 credit) — the shapes must stay
in lockstep with lib/searchapi_io's parser expectations.
"""

import json
from pathlib import Path

import pytest

from lib.serpapi_io import SOURCE_ID, SerpApiClient, _parse_point

FIXTURE = Path(__file__).parent / "fixtures" / "serpapi_flights_response.json"


@pytest.fixture()
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parses_best_flights_via_shared_parser(payload):
    options = _parse_point(payload)
    assert [o.price for o in options] == [567, 641]
    best = options[0]
    assert best.carriers == "Etihad"
    assert best.stops == 1              # two segments -> one layover
    assert best.total_minutes == 900


def test_falls_back_to_other_flights_when_no_best(payload):
    del payload["best_flights"]
    options = _parse_point(payload)
    assert [o.price for o in options] == [689]
    assert options[0].carriers == "EgyptAir"


def test_empty_response_parses_to_no_options():
    assert _parse_point({}) == []


def test_client_surface_matches_searchapi_duck_type():
    """run_followup duck-types on .source_id + .point_query — assert the
    surface without any network."""
    client = SerpApiClient(api_key="test-key")
    assert client.source_id == SOURCE_ID == "serpapi"
    assert callable(client.point_query)
    assert callable(client.check_quota)


def test_from_env_raises_without_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SERPAPI_KEY"):
        SerpApiClient.from_env()


def test_quota_normalization(monkeypatch):
    """check_quota normalizes /account fields without hitting the network."""
    class FakeResp:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"plan_searches_left": 93, "searches_per_month": 100,
                    "this_month_usage": 7}

    class FakeSession:
        def get(self, url, **kw):
            assert "account" in url
            return FakeResp()

    client = SerpApiClient(api_key="k", session=FakeSession())
    q = client.check_quota()
    assert q["remaining"] == 93
    assert q["limit_total"] == 100
    assert q["raw"]["this_month_usage"] == 7


def test_one_way_params_type_2_and_no_return_date():
    """One-way = type 2 with return_date OMITTED entirely — an empty
    value makes SerpAPI answer with an error payload."""
    from datetime import date

    client = SerpApiClient(api_key="test-key")
    seen: dict = {}

    class _Resp:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"best_flights": []}

    class _Session:
        def get(self, url, params=None, timeout=None):
            seen.clear()
            seen.update(params or {})
            return _Resp()

    client._session = _Session()
    client.point_query(origin="MAD", destination="NBO",
                       outbound=date(2026, 9, 20), return_=None,
                       currency="EUR")
    assert seen["type"] == "2"
    assert "return_date" not in seen

    client.point_query(origin="MAD", destination="NBO",
                       outbound=date(2026, 9, 20),
                       return_=date(2026, 11, 20), currency="EUR")
    assert seen["type"] == "1"
    assert seen["return_date"] == "2026-11-20"
