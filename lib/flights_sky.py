"""flights-sky (ntd119 on RapidAPI) — a Skyscanner-data proxy.

The OTA-coverage family (2026-07-13 audit): Google-Flights-based sources
(SerpApi, fast-flights) under-report the cheap OTA-only fares that live
on Gotogate / Mytrip / Trip.com / Kiwi. Skyscanner aggregates those OTAs
directly, so a Skyscanner proxy is the one free path to that inventory —
and, per the sample response, it surfaces Kiwi.com self-transfer combos,
handing back the virtual-interlining coverage lost when the Kiwi proxy
was retired.

Design notes:
- The RESPONSE shape is locked against a real sample
  (tests/fixtures/flights_sky_detail.json): the OTA sellers are
  `data.itinerary.pricingOptions[].agents[].{name, price, isCarrier}`;
  the cheapest agent is often an OTA below the airline-direct price.
- The SEARCH request path (endpoints, entity-id resolution via
  auto-complete, param names) still needs one real *search-endpoint*
  sample to finalize — flagged inline. Until then this ships DISABLED in
  the registry (like the Kiwi slot): the parser is proven, the wiring is
  gated on Carlos confirming the RapidAPI plan is no-card + Hard Limit.
- This is a Skyscanner MIRROR: for the confidence model it counts as ONE
  "OTA-metasearch" family together with Sky-Scrapper — more mirrors add
  quota + failover, never independent coverage.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import requests

LOG = logging.getLogger(__name__)

SOURCE_ID = "flights_sky"
HOST = "flights-sky.p.rapidapi.com"
BASE_URL = f"https://{HOST}"
DEFAULT_TIMEOUT_S = 30


class FlightsSkyError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(f"flights-sky HTTP {status_code}: {message}")
        self.status_code = status_code


@dataclass(frozen=True)
class ScannerOption:
    """One priced itinerary from a Skyscanner-proxy result, with the
    cheapest booking SELLER (the OTA-coverage payload)."""
    price: int
    currency: str
    carriers: str            # "French Bee + Spirit Airlines"
    stops: int
    total_minutes: int | None
    is_self_transfer: bool
    seller: str              # cheapest agent, e.g. "Kiwi.com"
    seller_is_ota: bool      # True unless the seller is the airline itself
    departure_date: str      # YYYY-MM-DD
    return_date: str         # "" for one-way


class FlightsSkyClient:
    source_id = SOURCE_ID

    def __init__(self, api_key: str, *,
                 session: requests.Session | None = None,
                 timeout_s: int = DEFAULT_TIMEOUT_S):
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout_s = timeout_s

    @classmethod
    def from_env(cls, var: str = "RAPIDAPI_KEY", **kwargs) -> "FlightsSkyClient":
        key = os.environ.get(var, "").strip()
        if not key:
            raise RuntimeError(
                f"{var} is not set. Subscribe to flights-sky (ntd119) on "
                f"RapidAPI — confirm the BASIC plan needs NO card and is "
                f"'Hard Limit' (fails 429, never 402).")
        return cls(api_key=key, **kwargs)

    def _request(self, path: str, params: dict) -> dict:
        url = f"{BASE_URL}{path}"
        headers = {"x-rapidapi-key": self._api_key, "x-rapidapi-host": HOST}
        try:
            r = self._session.get(url, headers=headers, params=params,
                                  timeout=self._timeout_s)
        except requests.RequestException as exc:
            raise FlightsSkyError(0, f"network error: {exc}") from exc
        if not r.ok:
            raise FlightsSkyError(r.status_code, r.text[:300])
        try:
            data = r.json()
        except ValueError as exc:
            raise FlightsSkyError(r.status_code, "non-JSON response") from exc
        if not isinstance(data, dict) or data.get("status") is False:
            raise FlightsSkyError(
                r.status_code, str(data.get("message", "status=false")))
        return data

    # NOTE: the search request path (endpoint names, entity-id resolution
    # via /flights/auto-complete, exact params) needs one real search
    # sample to finalize — DO NOT guess it live. `search_roundtrip` /
    # `search_one_way` land once that sample exists; the parser below is
    # already proven against a real response.


def _cheapest_pricing(pricing_options: list) -> tuple[int, str, bool] | None:
    """(price, seller, seller_is_ota) for the cheapest booking option."""
    best = None
    for opt in pricing_options or []:
        if not isinstance(opt, dict):
            continue
        price = opt.get("totalPrice")
        agents = opt.get("agents") or []
        agent = agents[0] if agents and isinstance(agents[0], dict) else {}
        if price is None:
            price = agent.get("price")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        seller = agent.get("name") or "unknown"
        is_ota = not bool(agent.get("isCarrier", False))
        if best is None or price < best[0]:
            best = (int(round(price)), str(seller), is_ota)
    return best


def _carriers(legs: list) -> tuple[str, int, int | None, bool]:
    """(carriers, stops, total_minutes, is_self_transfer) from the legs."""
    names: list[str] = []
    total_min = 0
    has_min = False
    self_transfer = False
    stops = 0
    for i, leg in enumerate(legs or []):
        if not isinstance(leg, dict):
            continue
        if i == 0 and isinstance(leg.get("stopCount"), int):
            stops = leg["stopCount"]        # outbound leg's stop count
        if isinstance(leg.get("duration"), int):
            total_min += leg["duration"]
            has_min = True
        for seg in leg.get("segments") or []:
            mk = (seg.get("marketingCarrier") or {}).get("name")
            if mk and mk not in names:
                names.append(mk)
            for gtk in seg.get("goodToKnowItems") or []:
                badge = (gtk.get("badge") or {}).get("value", "")
                if "self transfer" in str(badge).lower():
                    self_transfer = True
    return " + ".join(names), stops, (total_min if has_min else None), self_transfer


def _parse_itinerary(itin: dict, currency: str) -> ScannerOption | None:
    if not isinstance(itin, dict):
        return None
    legs = itin.get("legs") or []
    if not legs:
        return None
    cheap = _cheapest_pricing(itin.get("pricingOptions") or [])
    if cheap is None:
        return None
    price, seller, seller_is_ota = cheap
    carriers, stops, total_min, seg_self = _carriers(legs)
    self_transfer = bool(itin.get("isTransferRequired")) or seg_self
    dep = str((legs[0] or {}).get("departure", ""))[:10]
    ret = str((legs[1] or {}).get("departure", ""))[:10] if len(legs) > 1 else ""
    if not dep:
        return None
    return ScannerOption(
        price=price, currency=currency.upper(), carriers=carriers,
        stops=stops, total_minutes=total_min, is_self_transfer=self_transfer,
        seller=seller, seller_is_ota=seller_is_ota,
        departure_date=dep, return_date=ret)


def parse_itineraries(payload: dict, currency: str) -> list[ScannerOption]:
    """Parse a flights-sky response into priced itineraries with their
    cheapest OTA seller. Handles both the search shape
    (`data.itineraries[]`) and the single-itinerary detail shape
    (`data.itinerary`)."""
    data = (payload or {}).get("data") or {}
    itins = data.get("itineraries")
    if itins is None and data.get("itinerary") is not None:
        itins = [data["itinerary"]]
    out: list[ScannerOption] = []
    for itin in itins or []:
        opt = _parse_itinerary(itin, currency)
        if opt is not None:
            out.append(opt)
    return out
