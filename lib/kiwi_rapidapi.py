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

    # --- range search: multi-week discovery in ONE call ------------------

    def range_search(
        self,
        *,
        origin: str,
        destination: str,
        outbound_start: date,
        outbound_end: date,
        inbound_start: date,
        inbound_end: date,
        currency: str,
        adults: int = 1,
        limit: int = 50,
    ) -> KiwiResponse:
        """Discovery search across WIDE date ranges in a single call.

        Kiwi's endpoint natively accepts arbitrary date ranges for both
        legs; with sortBy=PRICE and a high limit, one call returns the
        cheapest ~50 itineraries across the whole band — price, exact
        dates, carriers, and the virtual-interlining flag included.
        This makes Kiwi a grid-discovery engine at 1 call per band
        (vs. 1 call per date-pair for point-query APIs).

        Callers derive the inbound band from the stay range:
        inbound_start = outbound_start + min_stay,
        inbound_end   = outbound_end + max_stay.
        Out-of-stay-range results are filtered downstream (same
        store-everything philosophy as the SearchAPI grid).
        """
        params: dict[str, Any] = {
            "source": origin,
            "destination": destination,
            "currency": currency.upper(),
            "outboundDepartureDateStart": f"{outbound_start.isoformat()}T00:00:00",
            "outboundDepartureDateEnd": f"{outbound_end.isoformat()}T23:59:59",
            "inboundDepartureDateStart": f"{inbound_start.isoformat()}T00:00:00",
            "inboundDepartureDateEnd": f"{inbound_end.isoformat()}T23:59:59",
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
    """Parse `itineraries[]` from a Kiwi round-trip response.

    Real shape (verified via probe response 2026-06-10):
        {
          "__typename": "Itineraries",
          "metadata": {...},
          "itineraries": [
            {
              "price": {"amount": "1101", "priceBeforeDiscount": "1101"},
              "priceEur": {"amount": "1101"},
              "travelHack": {
                "isVirtualInterlining": true,
                "isTrueHiddenCity": false,
                "isThrowawayTicket": false
              },
              "outbound": {
                "duration": 84900,   # seconds
                "sectorSegments": [
                  {"segment": {"carrier": {"code": "FR"},
                               "source": {"localTime": "2026-10-15T12:30:00",
                                          "station": {"code": "MAD"}},
                               "destination": {"localTime": "...",
                                               "station": {"code": "..."}}}},
                  ... more segments
                ]
              },
              "inbound": { ... same shape ... }
            }
          ]
        }
    """
    items = payload.get("itineraries")
    if not isinstance(items, list):
        # Fallback for older listing variants.
        items = payload.get("data") or payload.get("flights") or []
    if not isinstance(items, list):
        return []

    out: list[KiwiOption] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        # Price: prefer EUR-normalized amount; fall back to local price.
        price_raw = (
            ((it.get("priceEur") or {}).get("amount"))
            or ((it.get("price") or {}).get("amount"))
            or it.get("price")
        )
        try:
            price = int(round(float(price_raw)))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue

        # Legs.
        outbound = it.get("outbound") or {}
        inbound = it.get("inbound") or {}
        ob_segs = outbound.get("sectorSegments") or []
        ib_segs = inbound.get("sectorSegments") or []
        if not isinstance(ob_segs, list) or not ob_segs:
            continue

        # Origin / destination from the first outbound segment + final
        # outbound segment respectively.
        first_seg = (ob_segs[0] or {}).get("segment") or {}
        last_seg = (ob_segs[-1] or {}).get("segment") or {}
        fly_from = ((first_seg.get("source") or {}).get("station") or {}).get("code")
        fly_to = ((last_seg.get("destination") or {}).get("station") or {}).get("code")
        if not fly_from or not fly_to:
            continue

        # Departure date = first outbound segment's source localTime.
        # Return date  = first inbound segment's source localTime.
        dep = _date_part((first_seg.get("source") or {}).get("localTime"))
        if not dep:
            continue
        ret = None
        if ib_segs:
            ib_first = (ib_segs[0] or {}).get("segment") or {}
            ret = _date_part((ib_first.get("source") or {}).get("localTime"))

        # Carriers: dedup across all segments of both legs.
        carriers = _carriers_from_segments(ob_segs + ib_segs)

        # Stops: total layovers across both legs.
        # N segments per leg -> N-1 layovers; sum across legs.
        stops = max(0, len(ob_segs) - 1) + max(0, len(ib_segs) - 1)

        # Total minutes: outbound + inbound duration (seconds -> minutes).
        secs = 0
        for leg in (outbound, inbound):
            d = leg.get("duration")
            if isinstance(d, (int, float)):
                secs += int(d)
        total_minutes = (secs // 60) if secs else None

        # Virtual interlining flag — Kiwi's own marker.
        travel_hack = it.get("travelHack") or {}
        is_vi = bool(travel_hack.get("isVirtualInterlining"))

        out.append(KiwiOption(
            price=price,
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


def _carriers_from_segments(segments: list) -> str:
    """Dedupe carrier codes across a flat list of sectorSegment dicts."""
    codes: list[str] = []
    for sg in segments:
        if not isinstance(sg, dict):
            continue
        seg = sg.get("segment") or {}
        carrier = seg.get("carrier") or {}
        code = carrier.get("code") if isinstance(carrier, dict) else None
        if isinstance(code, str) and code and code not in codes:
            codes.append(code)
    return " + ".join(codes) if codes else "unknown"


# Kept for the legacy parser tests until they're updated to the new shape.
def _carriers_from_kiwi(itinerary: dict) -> str:
    """Back-compat alias; the new code uses _carriers_from_segments."""
    if "sectorSegments" in (itinerary.get("outbound") or {}):
        return _carriers_from_segments(
            (itinerary.get("outbound") or {}).get("sectorSegments", [])
            + (itinerary.get("inbound") or {}).get("sectorSegments", [])
        )
    # Old top-level shape fallback.
    codes: list[str] = []
    for code in (itinerary.get("airlines") or []):
        if isinstance(code, str) and code and code not in codes:
            codes.append(code)
    for seg in (itinerary.get("route") or []):
        if isinstance(seg, dict):
            code = seg.get("airline") or seg.get("airlineCode")
            if isinstance(code, str) and code and code not in codes:
                codes.append(code)
    return " + ".join(codes) if codes else "unknown"


def _date_part(iso_or_none) -> str | None:
    if not isinstance(iso_or_none, str) or len(iso_or_none) < 10:
        return None
    return iso_or_none[:10]
