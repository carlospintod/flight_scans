"""Travelpayouts / Aviasales Data API client.

Free, self-serve API that surfaces prices Google Flights doesn't —
particularly **Saudia (SV)** and other Middle East / North Africa
carriers that don't sell through Google's marketplace.

API token is auto-issued at signup on travelpayouts.com and lives in
.env as `TRAVELPAYOUTS_TOKEN`. No human approval needed.

Endpoints used:

* `/v3/prices_for_dates` — cached round-trip prices for a specific
  (origin, destination, depart_date, return_date). Light, cached on
  Aviasales' side; soft rate-limit only.
* `/v1/prices/cheap` — cheapest current price per (origin, destination)
  found across recent Aviasales user searches.
* `/v2/prices/latest` — most recently observed prices, broadly across
  a route. Good for discovering carriers we haven't seen on a date.

All endpoints return `{"success": true, "data": [...]}`. The shape of
items inside `data` is documented (see Travelpayouts docs) — but we
parse defensively because Aviasales has historically renamed fields
between versions.

This client follows the same pattern as `lib/searchapi_io.py` and
`lib/skyscanner_rapidapi.py`: parse JSON into typed dataclasses, raise
on errors, expose a `latest_quota` dict populated from response headers
when Aviasales sets `X-RateLimit-*` (it sometimes does, sometimes
doesn't — we degrade gracefully when headers are absent).

Reference: https://travelpayouts.github.io/slate/
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

LOG = logging.getLogger(__name__)

BASE_URL = "https://api.travelpayouts.com"
SOURCE_ID = "aviasales"
DEFAULT_TIMEOUT_S = 30
DEFAULT_CURRENCY = "eur"  # Aviasales uses lowercase currency codes


class AviasalesError(RuntimeError):
    def __init__(self, status_code: int, message: str, *, payload: Any = None):
        super().__init__(f"aviasales HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload


@dataclass(frozen=True)
class PriceQuote:
    """One Aviasales cached price observation.

    Fields preserve what's universally present across the three
    endpoints we use. `airline` is the IATA carrier code (e.g. 'SV'
    for Saudia, 'KQ' for Kenya Airways).
    """
    origin: str
    destination: str
    departure_date: str        # YYYY-MM-DD
    return_date: str | None    # YYYY-MM-DD or None for one-way responses
    price: int
    currency: str
    airline: str | None        # IATA carrier code
    flight_number: str | None
    found_at: str | None       # ISO timestamp Aviasales last saw this price
    expires_at: str | None     # ISO timestamp Aviasales' cache expires


@dataclass(frozen=True)
class PriceResponse:
    raw: dict
    quotes: tuple[PriceQuote, ...]


class AviasalesClient:
    """Travelpayouts/Aviasales Data API client.

    No DB connection needed for this client — Aviasales has no airport
    cache equivalent (it takes IATA codes directly) and the quota
    surface is small enough that the UI tracks it directly.
    """

    def __init__(
        self,
        token: str,
        *,
        session: requests.Session | None = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ):
        if not token:
            raise ValueError("token is required")
        self._token = token
        self._session = session or requests.Session()
        self._timeout_s = timeout_s
        self.latest_quota: dict | None = None

    @classmethod
    def from_env(cls, var: str = "TRAVELPAYOUTS_TOKEN", **kwargs) -> "AviasalesClient":
        token = os.environ.get(var, "").strip()
        if not token:
            raise RuntimeError(
                f"{var} is not set. Sign up at travelpayouts.com, "
                f"copy your token, and put it in .env."
            )
        return cls(token=token, **kwargs)

    # --- prices_for_dates: round-trip price for one specific date pair --

    def prices_for_dates(
        self,
        *,
        origin: str,
        destination: str,
        depart_date: date,
        return_date: date | None = None,
        currency: str = DEFAULT_CURRENCY,
        one_way: bool = False,
    ) -> PriceResponse:
        """Cached price for a specific (origin, destination, dep, ret).

        Cheap and forgiving — Travelpayouts serves it from cache.
        """
        params: dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "departure_at": depart_date.isoformat(),
            "currency": currency.lower(),
            "one_way": str(one_way).lower(),
            "limit": 30,
            "sorting": "price",
        }
        if return_date is not None and not one_way:
            params["return_at"] = return_date.isoformat()
        data = self._request("/aviasales/v3/prices_for_dates", params)
        return PriceResponse(
            raw=data,
            quotes=tuple(_parse_quotes(
                data, currency,
                origin_default=origin, destination_default=destination,
            )),
        )

    # --- one-way: cheapest cached ticket per departure day in a month --

    def one_way_month_prices(
        self,
        *,
        origin: str,
        destination: str,
        month: str,
        currency: str = DEFAULT_CURRENCY,
    ) -> PriceResponse:
        """Cheapest cached ONE-WAY ticket per departure day of `month`
        ("YYYY-MM").

        /aviasales/v3/prices_for_dates with one_way=true groups by date:
        at most one ticket per departure day, no return_at on the items
        (probed 2026-07-08: MAD->NBO 2026-09 -> one item per cached day).
        limit=100 covers any month.
        """
        params: dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "departure_at": month,
            "currency": currency.lower(),
            "one_way": "true",
            "limit": 100,
            "sorting": "price",
        }
        data = self._request("/aviasales/v3/prices_for_dates", params)
        return PriceResponse(
            raw=data,
            quotes=tuple(_parse_quotes(
                data, currency,
                origin_default=origin, destination_default=destination,
            )),
        )

    # --- cheap: lowest current price per (origin, destination) ---------

    def cheap_prices(
        self,
        *,
        origin: str,
        destination: str,
        depart_date: date | None = None,
        return_date: date | None = None,
        currency: str = DEFAULT_CURRENCY,
    ) -> PriceResponse:
        """Cheapest currently-cached round-trip per (origin, destination).

        If depart_date is given, restricts to that departure month.
        Aviasales' response items don't carry origin (it's a request
        param), so we patch it in before parsing.
        """
        params: dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "currency": currency.lower(),
        }
        if depart_date is not None:
            params["depart_date"] = depart_date.strftime("%Y-%m")
        if return_date is not None:
            params["return_date"] = return_date.strftime("%Y-%m")
        data = self._request("/v1/prices/cheap", params, with_token_header=True)
        return PriceResponse(
            raw=data,
            quotes=tuple(_parse_quotes(data, currency,
                                       origin_default=origin,
                                       destination_default=destination)),
        )

    # --- latest: broad recent observations across a route --------------

    def latest_prices(
        self,
        *,
        origin: str,
        destination: str,
        depart_date_month: date | None = None,
        currency: str = DEFAULT_CURRENCY,
        limit: int = 30,
    ) -> PriceResponse:
        """Recently observed cheap prices on a route, across carriers.

        Useful for discovering which carriers serve a route — Saudia
        will surface here even when Google Flights skips it.
        """
        params: dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "currency": currency.lower(),
            "limit": limit,
            "sorting": "price",
        }
        if depart_date_month is not None:
            params["beginning_of_period"] = depart_date_month.strftime("%Y-%m-%d")
            params["period_type"] = "month"
        data = self._request("/v2/prices/latest", params, with_token_header=True)
        return PriceResponse(
            raw=data,
            quotes=tuple(_parse_quotes(
                data, currency,
                origin_default=origin, destination_default=destination,
            )),
        )

    # --- quota: best-effort ---------------------------------------------

    def check_quota(self) -> dict:
        """Travelpayouts doesn't have a /me endpoint.

        We do a tiny cheap_prices call as a probe and surface whatever
        rate-limit headers it returns. Returns a dict matching the
        shape of SearchAPI/Sky Scrapper quota responses.
        """
        try:
            self.cheap_prices(origin="MAD", destination="NBO")
        except AviasalesError:
            pass  # the headers are captured regardless; error is fine
        return self.latest_quota or {"remaining": None, "limit_total": None, "raw": {}}

    # --- internals -------------------------------------------------------

    def _request(
        self, path: str, params: dict, *, with_token_header: bool = False,
    ) -> dict:
        url = f"{BASE_URL}{path}"
        headers: dict[str, str] = {}
        if with_token_header:
            headers["X-Access-Token"] = self._token
        else:
            # /v3/prices_for_dates accepts token as query param.
            params = {**params, "token": self._token}
        LOG.info("aviasales GET %s params=%s", path,
                 {k: v for k, v in params.items() if k != "token"})
        try:
            r = self._session.get(
                url, params=params, headers=headers, timeout=self._timeout_s,
            )
        except requests.RequestException as exc:
            raise AviasalesError(0, f"network error: {exc}") from exc
        self._capture_quota(r)
        try:
            payload = r.json()
        except ValueError:
            payload = None
        if not r.ok:
            msg = ""
            if isinstance(payload, dict):
                msg = str(payload.get("error") or payload.get("message") or payload)
            else:
                msg = r.text[:500]
            raise AviasalesError(r.status_code, msg, payload=payload)
        if not isinstance(payload, dict):
            raise AviasalesError(r.status_code, "response was not a JSON object")
        # Aviasales returns `{"success": false, "error": "..."}` on soft errors.
        if payload.get("success") is False:
            raise AviasalesError(
                400, str(payload.get("error") or "success=false"), payload=payload,
            )
        return payload

    def _capture_quota(self, r: requests.Response) -> None:
        """Best-effort: capture rate-limit info if Aviasales returns it."""
        rem = r.headers.get("x-ratelimit-remaining") or r.headers.get("X-RateLimit-Remaining")
        tot = r.headers.get("x-ratelimit-limit") or r.headers.get("X-RateLimit-Limit")
        if rem is None and tot is None:
            return
        try:
            self.latest_quota = {
                "remaining": int(rem) if rem and str(rem).isdigit() else None,
                "limit_total": int(tot) if tot and str(tot).isdigit() else None,
                "raw": {k: v for k, v in r.headers.items()
                        if k.lower().startswith("x-ratelimit")},
            }
        except (TypeError, ValueError):
            pass


# --- top-level parsers ------------------------------------------------------


def _parse_quotes(
    payload: dict, currency: str,
    *,
    origin_default: str | None = None,
    destination_default: str | None = None,
) -> list[PriceQuote]:
    """Parse Aviasales response items into PriceQuote rows.

    Three different shapes show up across endpoints:

    A) /v3/prices_for_dates, /v2/prices/latest:
       data is a FLAT LIST of quote dicts.

    B) /v1/prices/cheap:
       data is a NESTED dict:
         {"NBO": {"1": {<quote>}, "2": {<quote>}, ...}, ...}
       — outer key is the destination IATA, inner keys are numeric
       string indices.

    We normalize both shapes into a single flat list before parsing
    individual quote items, then drop anything missing required fields.
    """
    raw_data = payload.get("data")
    items: list[dict] = []
    if isinstance(raw_data, list):
        items = [x for x in raw_data if isinstance(x, dict)]
    elif isinstance(raw_data, dict):
        # Shape B: {destination_iata: {"1": quote, "2": quote}}
        for dest_key, dest_block in raw_data.items():
            if not isinstance(dest_block, dict):
                continue
            for sub_key, quote in dest_block.items():
                if not isinstance(quote, dict):
                    continue
                # The destination IATA isn't carried inside each quote
                # in this shape — patch it in from the outer key.
                if "destination" not in quote:
                    quote = {**quote, "destination": dest_key}
                items.append(quote)
    out: list[PriceQuote] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        # Departure / return dates may be in a couple of shapes:
        # `departure_at`/`return_at` (ISO timestamps) or
        # `depart_date`/`return_date` (YYYY-MM-DD).
        dep = it.get("depart_date") or _date_part(it.get("departure_at"))
        ret = it.get("return_date") or _date_part(it.get("return_at"))
        price = it.get("price") or it.get("value")
        if not dep or not isinstance(price, (int, float)) or price <= 0:
            continue
        origin = (it.get("origin") or it.get("origin_airport")
                  or origin_default)
        destination = (it.get("destination") or it.get("destination_airport")
                       or destination_default)
        if not origin or not destination:
            continue
        airline = it.get("airline") or it.get("gate") or None
        flight_no = it.get("flight_number") or it.get("number") or None
        out.append(PriceQuote(
            origin=str(origin),
            destination=str(destination),
            departure_date=str(dep),
            return_date=str(ret) if ret else None,
            price=int(round(float(price))),
            currency=currency.upper(),
            airline=str(airline) if airline else None,
            flight_number=str(flight_no) if flight_no else None,
            found_at=it.get("found_at"),
            expires_at=it.get("expires_at"),
        ))
    return out


def _date_part(iso_or_none) -> str | None:
    """Extract YYYY-MM-DD from an ISO timestamp string, or None."""
    if not isinstance(iso_or_none, str) or len(iso_or_none) < 10:
        return None
    return iso_or_none[:10]
