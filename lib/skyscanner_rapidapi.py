"""Sky Scrapper (RapidAPI) client.

This is the second data source. It scrapes Skyscanner via the
`sky-scrapper.p.rapidapi.com` host. Two products are exposed:

* `calendar_curve(...)` — one call returns up to ~206 days of
  departure-date prices ("from £X" lowest round-trip per day,
  return date implicit). This is a discovery layer — cheap *per call*
  but doesn't tell us the return date for any given price.

* `point_query(...)` — `searchFlights` returns specific itineraries
  with carrier, stops, total duration, and a critical flag
  `is_self_transfer` indicating virtual interlining (multi-PNR
  bundles). The endpoint is eventually-consistent — kickoff returns
  status=incomplete with whatever results are ready; one poll with the
  sessionId usually yields the rest.

The adapter uses an airport-IATA → (skyId, entityId) cache stored in
the local DB. The first time a code is seen, we hit `searchAirport`
once and store the result. Subsequent runs cost zero lookup calls.

All probe-derived behaviors are documented in `probe_skyscanner.py`.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

LOG = logging.getLogger(__name__)

HOST = "sky-scrapper.p.rapidapi.com"
BASE_URL = f"https://{HOST}/api/v1/flights"
SOURCE_ID = "skyscanner"
DEFAULT_TIMEOUT_S = 60
POLL_WAIT_S = 4
MAX_POLL_ATTEMPTS = 1   # one poll after kickoff. Each poll = 1 call.


class SkyScrapperError(RuntimeError):
    def __init__(self, status_code: int, message: str, *, payload: Any = None):
        super().__init__(f"sky-scrapper HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload


@dataclass(frozen=True)
class CurveEntry:
    """One day in the departure-date pricing curve."""
    departure_date: str   # YYYY-MM-DD
    price: float
    price_group: str | None  # 'low' | 'medium' | 'high' | None


@dataclass(frozen=True)
class FlightOption:
    """One itinerary from the point-query result."""
    price: int                # round-trip total, rounded to int for parity
    total_minutes: int | None
    stops: int                # max stops across legs (outbound + return)
    carriers: str             # "Etihad Airways" or "KLM + Kenya Airways"
    is_self_transfer: bool


@dataclass(frozen=True)
class CurveResponse:
    raw: dict
    entries: tuple[CurveEntry, ...]


@dataclass(frozen=True)
class PointResponse:
    raw: dict
    best_flights: tuple[FlightOption, ...]


class SkyScrapperClient:
    """RapidAPI Sky Scrapper client.

    Note the `db_conn` argument: airport-code lookups are cached in the
    local DB so we don't burn API calls translating IATA→skyId on every
    run. Pass `None` only in tests where you control the airport ids
    via the `override_airports` argument.
    """

    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        db_conn=None,
        override_airports: dict[str, tuple[str, str]] | None = None,
    ):
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout_s = timeout_s
        self._db_conn = db_conn
        self._override = override_airports or {}

    @classmethod
    def from_env(cls, var: str = "RAPIDAPI_KEY", **kwargs) -> "SkyScrapperClient":
        key = os.environ.get(var, "").strip()
        if not key:
            raise RuntimeError(
                f"{var} is not set. Put it in .env or export it in your shell."
            )
        return cls(api_key=key, **kwargs)

    # --- airport resolution --------------------------------------------

    def resolve_airport(self, iata: str) -> tuple[str, str]:
        """Return (skyId, entityId) for an IATA airport code.

        First checks the override map, then the DB cache (if available),
        then hits the searchAirport endpoint. Costs 1 API call only on
        first sighting of a given IATA code.
        """
        if iata in self._override:
            return self._override[iata]
        if self._db_conn is not None:
            from . import db as db_mod
            cached = db_mod.lookup_airport(self._db_conn, iata)
            if cached is not None:
                return cached
        data = self._request("searchAirport", {"query": iata, "locale": "en-US"})
        sky, ent, name = _pick_airport(data, iata)
        if self._db_conn is not None:
            from . import db as db_mod
            db_mod.store_airport(self._db_conn, iata, sky, ent, name)
        return sky, ent

    # --- calendar curve ------------------------------------------------

    def calendar_curve(
        self, *, origin: str, destination: str, from_date: date, currency: str,
    ) -> CurveResponse:
        """Single API call, returns the year-long departure-date price curve."""
        o_sky, o_ent = self.resolve_airport(origin)
        d_sky, d_ent = self.resolve_airport(destination)
        params = {
            "originSkyId": o_sky,
            "destinationSkyId": d_sky,
            "originEntityId": o_ent,
            "destinationEntityId": d_ent,
            "fromDate": from_date.isoformat(),
            "currency": currency,
        }
        data = self._request("getPriceCalendar", params)
        entries = tuple(_parse_curve(data))
        return CurveResponse(raw=data, entries=entries)

    # --- point query (round-trip search) -------------------------------

    def point_query(
        self,
        *,
        origin: str,
        destination: str,
        outbound: date,
        return_: date,
        currency: str,
        adults: int = 1,
        country_code: str = "ES",
        market: str = "es-ES",
        max_polls: int = MAX_POLL_ATTEMPTS,
    ) -> PointResponse:
        """Round-trip search via searchFlights with optional polling.

        First call may return status=incomplete with partial results. We
        poll up to `max_polls` times before returning whatever we have.
        Each poll counts as one API call.
        """
        o_sky, o_ent = self.resolve_airport(origin)
        d_sky, d_ent = self.resolve_airport(destination)
        base_params: dict[str, Any] = {
            "originSkyId": o_sky,
            "destinationSkyId": d_sky,
            "originEntityId": o_ent,
            "destinationEntityId": d_ent,
            "date": outbound.isoformat(),
            "returnDate": return_.isoformat(),
            "adults": str(adults),
            "currency": currency,
            "countryCode": country_code,
            "market": market,
        }
        data = self._request("searchFlights", base_params)
        ctx = ((data or {}).get("data") or {}).get("context") or {}
        status = ctx.get("status")
        session_id = ctx.get("sessionId")
        polls = 0
        while status == "incomplete" and session_id and polls < max_polls:
            time.sleep(POLL_WAIT_S)
            data = self._request("searchFlights", {**base_params, "sessionId": session_id})
            ctx = ((data or {}).get("data") or {}).get("context") or {}
            status = ctx.get("status")
            polls += 1
        best = tuple(_parse_flights(data))
        return PointResponse(raw=data, best_flights=best)

    # --- internals -----------------------------------------------------

    def _request(self, path: str, params: dict) -> dict:
        url = f"{BASE_URL}/{path}"
        headers = {"x-rapidapi-key": self._api_key, "x-rapidapi-host": HOST}
        LOG.info("skyscanner GET %s params=%s",
                 path, {k: v for k, v in params.items() if k != "sessionId"})
        try:
            r = self._session.get(url, headers=headers, params=params, timeout=self._timeout_s)
        except requests.RequestException as exc:
            raise SkyScrapperError(0, f"network error: {exc}") from exc
        try:
            payload = r.json()
        except ValueError:
            payload = None
        if not r.ok:
            msg = ""
            if isinstance(payload, dict):
                msg = str(payload.get("message") or payload)
            else:
                msg = r.text[:500]
            raise SkyScrapperError(r.status_code, msg, payload=payload)
        if not isinstance(payload, dict):
            raise SkyScrapperError(r.status_code, "response was not a JSON object")
        # Sky Scrapper signals errors with status=false at the top level.
        if payload.get("status") is False:
            raise SkyScrapperError(
                400, str(payload.get("message") or "status=false"), payload=payload,
            )
        return payload


# --- top-level parsers (testable without an HTTP client) -----------------


def _pick_airport(payload: dict, iata: str) -> tuple[str, str, str | None]:
    """Find the best matching airport entry for an IATA code.

    Preference order:
      1. AIRPORT-typed entity with skyId == iata
      2. Any entity with skyId == iata
      3. First AIRPORT-typed entity in the list (fallback)
    """
    items = payload.get("data") or []
    if not isinstance(items, list):
        raise SkyScrapperError(0, "searchAirport: data is not a list")

    def _flight_params(entry: dict) -> dict:
        return (entry.get("navigation") or {}).get("relevantFlightParams") or {}

    def _name(entry: dict) -> str | None:
        pres = entry.get("presentation") or {}
        return pres.get("suggestionTitle") or pres.get("title")

    # Pass 1: airport-typed with skyId == iata
    for entry in items:
        if not isinstance(entry, dict):
            continue
        nav = entry.get("navigation") or {}
        if nav.get("entityType") != "AIRPORT":
            continue
        fp = _flight_params(entry)
        if fp.get("skyId") == iata:
            return str(fp["skyId"]), str(fp["entityId"]), _name(entry)
    # Pass 2: any entity with skyId == iata
    for entry in items:
        if not isinstance(entry, dict):
            continue
        fp = _flight_params(entry)
        if fp.get("skyId") == iata:
            return str(fp["skyId"]), str(fp["entityId"]), _name(entry)
    # Pass 3: first airport-typed entity
    for entry in items:
        if not isinstance(entry, dict):
            continue
        nav = entry.get("navigation") or {}
        if nav.get("entityType") != "AIRPORT":
            continue
        fp = _flight_params(entry)
        if fp.get("skyId") and fp.get("entityId"):
            return str(fp["skyId"]), str(fp["entityId"]), _name(entry)
    raise SkyScrapperError(0, f"searchAirport: no match for {iata!r}")


def _parse_curve(payload: dict) -> list[CurveEntry]:
    days = (
        ((payload or {}).get("data") or {})
        .get("flights", {})
        .get("days", [])
    )
    out: list[CurveEntry] = []
    for d in days or []:
        if not isinstance(d, dict):
            continue
        day = d.get("day")
        price = d.get("price")
        if not isinstance(day, str) or price is None:
            continue
        try:
            price_f = float(price)
        except (TypeError, ValueError):
            continue
        if price_f <= 0:
            continue
        group = d.get("group") if isinstance(d.get("group"), str) else None
        out.append(CurveEntry(departure_date=day, price=price_f, price_group=group))
    return out


def _parse_flights(payload: dict) -> list[FlightOption]:
    itineraries = ((payload or {}).get("data") or {}).get("itineraries") or []
    out: list[FlightOption] = []
    for it in itineraries:
        if not isinstance(it, dict):
            continue
        price_obj = it.get("price") or {}
        raw_price = price_obj.get("raw")
        if not isinstance(raw_price, (int, float)):
            continue
        legs = it.get("legs") or []
        if not isinstance(legs, list) or not legs:
            continue
        total_minutes = sum(
            int(leg.get("durationInMinutes") or 0)
            for leg in legs if isinstance(leg, dict)
        ) or None
        # Per-leg stop counts; use the max across outbound + return as a proxy.
        stops_per_leg = [
            int(leg.get("stopCount") or 0)
            for leg in legs if isinstance(leg, dict)
        ]
        stops = max(stops_per_leg) if stops_per_leg else 0
        carriers = _carriers_from_legs(legs)
        is_self = bool(it.get("isSelfTransfer", False))
        out.append(FlightOption(
            price=int(round(float(raw_price))),
            total_minutes=total_minutes,
            stops=stops,
            carriers=carriers,
            is_self_transfer=is_self,
        ))
    # Sort by price ascending so callers can take the cheapest N.
    out.sort(key=lambda f: f.price)
    return out


def _carriers_from_legs(legs: list[Any]) -> str:
    names: list[str] = []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        c = leg.get("carriers") or {}
        for m in (c.get("marketing") or []):
            if isinstance(m, dict):
                n = m.get("name")
                if isinstance(n, str) and n and n not in names:
                    names.append(n)
    return " + ".join(names) if names else "unknown"
