"""Kiwi.com Cheap Flights (RapidAPI / emir12) client.

Unofficial scraper of Kiwi.com hosted on RapidAPI. Different from the
official Kiwi Tequila Partners program (which requires an affiliate
email — Carlos declined that earlier). This wrapper is self-serve via
RapidAPI subscribe; the API key is the standard `RAPIDAPI_KEY` already
in .env.

Why we want this in spite of having SearchAPI: **virtual interlining**.
Kiwi sells bundles of two separately-ticketed flights (e.g. Ryanair
MAD→DOH + Kenya Airways DOH→NBO) as one purchase with their own
guarantee. Google Flights and SearchAPI structurally can't show these —
they only sell carrier-bundled tickets. On Europe → Africa corridors
specifically these bundles can be 10-25% cheaper.

The free tier is small (~100/mo typical for RapidAPI BASIC plans). We
treat Kiwi like Sky Scrapper: passively capture rate-limit headers from
every response, persist a snapshot to the DB.

API quota is SEPARATE from the Sky Scrapper quota even though they
share the same RAPIDAPI_KEY — each RapidAPI listing has its own counter.

Reference: https://rapidapi.com/emir12/api/kiwi-com-cheap-flights
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

LOG = logging.getLogger(__name__)

HOST = "kiwi-com-cheap-flights.p.rapidapi.com"
BASE_URL = f"https://{HOST}"
SOURCE_ID = "kiwi"
DEFAULT_TIMEOUT_S = 60


class KiwiError(RuntimeError):
    def __init__(self, status_code: int, message: str, *, payload: Any = None):
        super().__init__(f"kiwi HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload


@dataclass(frozen=True)
class KiwiOption:
    """One Kiwi itinerary, condensed.

    `is_virtual_interlining` is the critical flag — it tells us this
    is a Kiwi-orchestrated bundle of separately-ticketed flights, not
    a single airline-issued ticket. These are the deals Google Flights
    cannot show.
    """
    price: int
    currency: str
    fly_from: str               # origin IATA
    fly_to: str                 # destination IATA
    depart_date: str            # YYYY-MM-DD
    return_date: str | None
    carriers: str               # "Ryanair + Kenya Airways" or "Etihad"
    total_minutes: int | None
    stops: int
    is_virtual_interlining: bool


@dataclass(frozen=True)
class KiwiResponse:
    raw: dict
    options: tuple[KiwiOption, ...]


class KiwiClient:
    """Kiwi.com Cheap Flights wrapper on RapidAPI."""

    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        db_conn=None,
    ):
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout_s = timeout_s
        self._db_conn = db_conn
        self.latest_quota: dict | None = None

    @classmethod
    def from_env(cls, var: str = "RAPIDAPI_KEY", **kwargs) -> "KiwiClient":
        key = os.environ.get(var, "").strip()
        if not key:
            raise RuntimeError(
                f"{var} is not set. Subscribe to "
                f"https://rapidapi.com/emir12/api/kiwi-com-cheap-flights "
                f"and put your RapidAPI key in .env."
            )
        return cls(api_key=key, **kwargs)

    # --- round-trip search ----------------------------------------------

    def round_trip_search(
        self,
        *,
        origin: str,
        destination: str,
        depart_date: date,
        return_date: date,
        currency: str,
        adults: int = 1,
        limit: int = 20,
    ) -> KiwiResponse:
        """Round-trip search. Returns the cheapest N itineraries.

        Kiwi's RapidAPI surface is single-call (no async polling like
        Sky Scrapper). The response includes a list of itineraries
        each tagged with whether it's a virtual-interlining bundle.
        """
        params: dict[str, Any] = {
            "source": origin,
            "destination": destination,
            "currency": currency.upper(),
            "outboundDepartureDateStart": f"{depart_date.isoformat()}T00:00:00",
            "outboundDepartureDateEnd": f"{depart_date.isoformat()}T23:59:59",
            "inboundDepartureDateStart": f"{return_date.isoformat()}T00:00:00",
            "inboundDepartureDateEnd": f"{return_date.isoformat()}T23:59:59",
            "adults": str(adults),
            "limit": str(limit),
            "sortBy": "PRICE",
        }
        data = self._request("/round-trip", params)
        return KiwiResponse(raw=data, options=tuple(_parse_options(data, currency)))

    # --- one-way (cheap, useful for discovery) --------------------------

    def one_way_search(
        self,
        *,
        origin: str,
        destination: str,
        depart_date: date,
        currency: str,
        adults: int = 1,
        limit: int = 20,
    ) -> KiwiResponse:
        params: dict[str, Any] = {
            "source": origin,
            "destination": destination,
            "currency": currency.upper(),
            "outboundDepartureDateStart": f"{depart_date.isoformat()}T00:00:00",
            "outboundDepartureDateEnd": f"{depart_date.isoformat()}T23:59:59",
            "adults": str(adults),
            "limit": str(limit),
            "sortBy": "PRICE",
        }
        data = self._request("/one-way", params)
        return KiwiResponse(raw=data, options=tuple(_parse_options(data, currency)))

    # --- internals -------------------------------------------------------

    def _request(self, path: str, params: dict) -> dict:
        url = f"{BASE_URL}{path}"
        headers = {"x-rapidapi-key": self._api_key, "x-rapidapi-host": HOST}
        LOG.info("kiwi GET %s params=%s", path, params)
        try:
            r = self._session.get(
                url, headers=headers, params=params, timeout=self._timeout_s,
            )
        except requests.RequestException as exc:
            raise KiwiError(0, f"network error: {exc}") from exc
        self._capture_quota(r)
        try:
            payload = r.json()
        except ValueError:
            payload = None
        if not r.ok:
            msg = ""
            if isinstance(payload, dict):
                msg = str(payload.get("message") or payload.get("error") or payload)
            else:
                msg = r.text[:500]
            raise KiwiError(r.status_code, msg, payload=payload)
        if not isinstance(payload, dict):
            raise KiwiError(r.status_code, "response was not a JSON object")
        return payload

    def _capture_quota(self, r: requests.Response) -> None:
        """Pull RapidAPI rate-limit headers; persist a snapshot to DB."""
        rem = r.headers.get("x-ratelimit-requests-remaining")
        tot = r.headers.get("x-ratelimit-requests-limit")
        if rem is None and tot is None:
            return
        self.latest_quota = {
            "remaining": int(rem) if rem is not None and rem.isdigit() else None,
            "limit_total": int(tot) if tot is not None and tot.isdigit() else None,
            "raw": {k: v for k, v in r.headers.items()
                    if k.lower().startswith("x-ratelimit")},
        }
        if self._db_conn is not None:
            try:
                from . import db as db_mod
                db_mod.record_quota(
                    self._db_conn, source=SOURCE_ID,
                    remaining=self.latest_quota["remaining"],
                    limit_total=self.latest_quota["limit_total"],
                    raw_json=json.dumps(self.latest_quota["raw"]),
                )
            except Exception as exc:  # noqa: BLE001
                LOG.warning("kiwi: failed to persist quota snapshot: %s", exc)


# --- top-level parsers ------------------------------------------------------


def _parse_options(payload: dict, currency: str) -> list[KiwiOption]:
    """Parse itineraries from a Kiwi response.

    Kiwi/emir12's response keys can vary by endpoint version. We try
    `itineraries`, `data`, and `flights` in order to find the array.
    Inside each item we look for the standard Kiwi field set.
    """
    items = (
        payload.get("itineraries")
        or payload.get("data")
        or payload.get("flights")
        or []
    )
    if not isinstance(items, list):
        return []
    out: list[KiwiOption] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        price = (
            it.get("price")
            or (it.get("price", {}) or {}).get("amount") if isinstance(it.get("price"), dict)
            else it.get("price")
        )
        if isinstance(price, dict):
            price = price.get("amount") or price.get("value")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        fly_from = it.get("flyFrom") or it.get("fly_from") or it.get("source")
        fly_to = it.get("flyTo") or it.get("fly_to") or it.get("destination")
        if not fly_from or not fly_to:
            continue
        dep = _date_part(
            it.get("local_departure")
            or it.get("localDeparture")
            or it.get("outboundDepartureDate")
            or it.get("departure_date")
        )
        ret = _date_part(
            it.get("local_arrival_inbound")
            or it.get("inboundDepartureDate")
            or it.get("return_date")
        )
        if not dep:
            continue
        # Carriers — Kiwi nests them under `route[]` or `airlines`.
        carriers = _carriers_from_kiwi(it)
        total_minutes = it.get("duration")
        if isinstance(total_minutes, dict):
            total_minutes = total_minutes.get("total")
        if not isinstance(total_minutes, int):
            total_minutes = None
        # Stop count — Kiwi has `nightsInDest`, `route` length, or
        # an explicit `stops` count. Fall back to route length - 1.
        route = it.get("route") or []
        stops = max(0, len(route) - 1) if isinstance(route, list) else 0
        is_vi = bool(
            it.get("virtual_interlining")
            or it.get("isVirtualInterlining")
            or it.get("has_airport_change") is False and (it.get("price_dropdown") or {}).get("base_fare")
        )
        out.append(KiwiOption(
            price=int(round(float(price))),
            currency=currency.upper(),
            fly_from=str(fly_from),
            fly_to=str(fly_to),
            depart_date=str(dep),
            return_date=str(ret) if ret else None,
            carriers=carriers,
            total_minutes=total_minutes,
            stops=stops,
            is_virtual_interlining=is_vi,
        ))
    out.sort(key=lambda o: o.price)
    return out


def _carriers_from_kiwi(itinerary: dict) -> str:
    """Join unique carrier names/codes from a Kiwi itinerary."""
    # Try a few shapes Kiwi uses.
    names: list[str] = []
    # Shape A: top-level `airlines: ["FR", "KQ"]`
    al = itinerary.get("airlines")
    if isinstance(al, list):
        for code in al:
            if isinstance(code, str) and code and code not in names:
                names.append(code)
    # Shape B: nested `route[]` with `airline` field per segment
    route = itinerary.get("route") or []
    if isinstance(route, list):
        for seg in route:
            if not isinstance(seg, dict):
                continue
            code = seg.get("airline") or seg.get("airlineCode")
            if isinstance(code, str) and code and code not in names:
                names.append(code)
    return " + ".join(names) if names else "unknown"


def _date_part(iso_or_none) -> str | None:
    if not isinstance(iso_or_none, str) or len(iso_or_none) < 10:
        return None
    return iso_or_none[:10]
