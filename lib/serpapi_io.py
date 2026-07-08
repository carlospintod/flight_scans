"""SerpAPI client — managed Google Flights data, free 250 searches/month.

Why it exists: the free renewing tier is the cloud verification rail for
GitHub Actions scans. Unlike the local googleflights scraper it works
from any IP; unlike SearchAPI its free credits renew monthly.

The response JSON is the same Google Flights family as SearchAPI.io's
(`best_flights[].price / total_duration / flights[].airline`), so this
module reuses lib/searchapi_io's parser and dataclasses. One divergence
handled here: when Google doesn't rank a "best" group, SerpAPI returns
only `other_flights` — parsed with the same code path.

Request params differ slightly from SearchAPI: `type=1` (round trip)
instead of `flight_type=round_trip`, and auth is `api_key` only.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

import requests

from .searchapi_io import PointResponse, _parse_best_flights

LOG = logging.getLogger(__name__)

BASE_URL = "https://serpapi.com/search"
ACCOUNT_URL = "https://serpapi.com/account"
DEFAULT_TIMEOUT_S = 90
SOURCE_ID = "serpapi"


class SerpApiError(RuntimeError):
    """Raised when the SerpAPI response is unusable."""

    def __init__(self, status_code: int, message: str, *, payload: Any = None):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload


class SerpApiClient:
    """Point-query client with the same surface as SearchApiClient."""

    source_id = SOURCE_ID  # rows produced by this client get this tag

    def __init__(self, api_key: str, *, session: requests.Session | None = None,
                 timeout_s: int = DEFAULT_TIMEOUT_S):
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout_s = timeout_s

    @classmethod
    def from_env(cls, var: str = "SERPAPI_KEY") -> "SerpApiClient":
        key = os.environ.get(var, "").strip()
        if not key:
            raise RuntimeError(
                f"{var} is not set. Sign up free at serpapi.com (250 searches"
                "/month) and put the key in .env or the CI secrets."
            )
        return cls(api_key=key)

    def point_query(
        self,
        *,
        origin: str,
        destination: str,
        outbound: date,
        return_: date | None,
        currency: str,
        adults: int = 1,
        extra: dict[str, Any] | None = None,
    ) -> PointResponse:
        """Single point search; one-way when return_ is None.

        Returns parsed best_flights[0..]. One-way is type=2 and the
        return_date key must be OMITTED entirely — an empty value makes
        SerpAPI answer with an error payload.
        """
        params: dict[str, Any] = {
            "engine": "google_flights",
            "api_key": self._api_key,
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": outbound.isoformat(),
            "type": "1" if return_ is not None else "2",  # 1=round trip, 2=one way
            "currency": currency,
            "adults": adults,
            "hl": "en",
        }
        if return_ is not None:
            params["return_date"] = return_.isoformat()
        if extra:
            params.update(extra)
        LOG.info("serpapi GET %s->%s dep=%s ret=%s",
                 origin, destination, outbound, return_)
        try:
            r = self._session.get(BASE_URL, params=params,
                                  timeout=self._timeout_s)
        except requests.RequestException as exc:
            raise SerpApiError(0, f"network error: {exc}") from exc
        try:
            data = r.json()
        except ValueError as exc:
            raise SerpApiError(r.status_code, "non-JSON response") from exc
        if not r.ok or "error" in data:
            raise SerpApiError(r.status_code,
                               str(data.get("error", r.text[:200])),
                               payload=data)
        return PointResponse(raw=data, best_flights=tuple(_parse_point(data)))

    def check_quota(self) -> dict:
        """Return the current SerpAPI quota (does not count as a search).

        GET /account returns e.g.:
            {"plan_searches_left": 93, "searches_per_month": 100,
             "this_month_usage": 7, ...}
        Normalized to {remaining, limit_total, raw} like the other clients.
        """
        try:
            r = self._session.get(ACCOUNT_URL,
                                  params={"api_key": self._api_key},
                                  timeout=15)
        except requests.RequestException as exc:
            raise SerpApiError(0, f"network error: {exc}") from exc
        if not r.ok:
            raise SerpApiError(r.status_code,
                               f"/account returned {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError as exc:
            raise SerpApiError(r.status_code, "non-JSON response from /account") from exc
        remaining = data.get("plan_searches_left")
        # total_searches_left includes purchased top-ups; plan_searches_left
        # is the renewing monthly pool we budget against.
        limit_total = data.get("searches_per_month")
        return {
            "remaining": remaining if isinstance(remaining, int) else None,
            "limit_total": limit_total if isinstance(limit_total, int) else None,
            "raw": data,
        }


def _parse_point(payload: dict) -> list:
    """best_flights, falling back to other_flights (same documented shape).

    SerpAPI omits best_flights entirely when Google doesn't rank a "best"
    group for the query — the results are all in other_flights then.
    """
    options = _parse_best_flights(payload)
    if not options and payload.get("other_flights"):
        options = _parse_best_flights({"best_flights": payload["other_flights"]})
    return options
