"""SearchAPI.io client for the two engines we use.

* `google_flights_calendar` — price grid across a date rectangle. Capped at
  200 (departure x return) combinations per call.
* `google_flights` — point query for one specific (outbound, return) pair.

Both share the same `/search` endpoint and differ only by the `engine`
query parameter. The API key is read from the `SEARCHAPI_KEY` env var
(loaded from `.env` by `tracker.py`).

This module only fetches and shapes responses into typed records the rest
of the system consumes. It does no persistence and no business logic.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

LOG = logging.getLogger(__name__)

BASE_URL = "https://www.searchapi.io/api/v1/search"
CALENDAR_COMBO_CAP = 200
DEFAULT_TIMEOUT_S = 95  # API docs say 503 after 90s
SOURCE_ID = "searchapi"


class SearchApiError(RuntimeError):
    """Raised when the SearchAPI.io response is unusable."""

    def __init__(self, status_code: int, message: str, *, payload: Any = None):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload


@dataclass(frozen=True)
class CalendarEntry:
    """One cell in the calendar grid."""
    departure_date: str   # YYYY-MM-DD
    return_date: str      # YYYY-MM-DD
    price: int
    has_no_flights: bool
    is_lowest_price: bool


@dataclass(frozen=True)
class FlightOption:
    """One best_flights entry from the point query, condensed."""
    price: int
    total_minutes: int | None
    stops: int                # number of layovers
    carriers: str             # "Qatar Airways" or "KLM + Kenya Airways"


@dataclass(frozen=True)
class CalendarResponse:
    raw: dict
    entries: tuple[CalendarEntry, ...]


@dataclass(frozen=True)
class PointResponse:
    raw: dict
    best_flights: tuple[FlightOption, ...]


class SearchApiClient:
    """Thin wrapper. Construct with the key, or `from_env()` to read it."""

    def __init__(self, api_key: str, *, session: requests.Session | None = None,
                 timeout_s: int = DEFAULT_TIMEOUT_S):
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout_s = timeout_s

    @classmethod
    def from_env(cls, var: str = "SEARCHAPI_KEY") -> "SearchApiClient":
        key = os.environ.get(var, "").strip()
        if not key:
            raise RuntimeError(
                f"{var} is not set. Put it in .env or export it in your shell."
            )
        return cls(api_key=key)

    # --- calendar engine -------------------------------------------------

    def calendar(
        self,
        *,
        origin: str,
        destination: str,
        outbound_start: date,
        outbound_end: date,
        return_start: date,
        return_end: date,
        currency: str,
        adults: int = 1,
        stops: str = "any",
        extra: dict[str, Any] | None = None,
    ) -> CalendarResponse:
        """Run a calendar (rectangle) search.

        Raises `SearchApiError` if the combination cap is exceeded or any
        HTTP error occurs. The 200-cap check is intentionally enforced
        client-side too so we fail fast before burning a call.
        """
        combos = (_days_inclusive(outbound_start, outbound_end)
                  * _days_inclusive(return_start, return_end))
        if combos > CALENDAR_COMBO_CAP:
            raise SearchApiError(
                400,
                f"calendar request would request {combos} combos, "
                f"max is {CALENDAR_COMBO_CAP}",
            )
        params: dict[str, Any] = {
            "engine": "google_flights_calendar",
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": outbound_start.isoformat(),
            "return_date": return_start.isoformat(),
            "outbound_date_start": outbound_start.isoformat(),
            "outbound_date_end": outbound_end.isoformat(),
            "return_date_start": return_start.isoformat(),
            "return_date_end": return_end.isoformat(),
            "flight_type": "round_trip",
            "currency": currency,
            "adults": adults,
            "stops": stops,
        }
        if extra:
            params.update(extra)
        data = self._request(params)
        entries = tuple(_parse_calendar_entries(data))
        return CalendarResponse(raw=data, entries=entries)

    # --- point-query engine ---------------------------------------------

    def point_query(
        self,
        *,
        origin: str,
        destination: str,
        outbound: date,
        return_: date,
        currency: str,
        adults: int = 1,
        stops: str = "any",
        extra: dict[str, Any] | None = None,
    ) -> PointResponse:
        """Single-itinerary search.

        Returns the parsed `best_flights[0..]` list. The raw response is
        also exposed for callers that want richer fields.
        """
        params: dict[str, Any] = {
            "engine": "google_flights",
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": outbound.isoformat(),
            "return_date": return_.isoformat(),
            "flight_type": "round_trip",
            "currency": currency,
            "adults": adults,
            "stops": stops,
        }
        if extra:
            params.update(extra)
        data = self._request(params)
        best = tuple(_parse_best_flights(data))
        return PointResponse(raw=data, best_flights=best)

    # --- internals -------------------------------------------------------

    def _request(self, params: dict[str, Any]) -> dict:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        LOG.info(
            "searchapi engine=%s params=%s",
            params.get("engine"),
            {k: v for k, v in params.items() if k not in {"engine"}},
        )
        try:
            resp = self._session.get(
                BASE_URL, params=params, headers=headers, timeout=self._timeout_s,
            )
        except requests.RequestException as exc:
            raise SearchApiError(0, f"network error: {exc}") from exc

        try:
            payload = resp.json()
        except ValueError:
            payload = None

        if not resp.ok:
            msg = ""
            if isinstance(payload, dict):
                msg = str(payload.get("error") or payload)
            else:
                msg = resp.text[:500]
            raise SearchApiError(resp.status_code, msg, payload=payload)

        if not isinstance(payload, dict):
            raise SearchApiError(resp.status_code, "response body was not a JSON object")
        # Some no-result responses come back 200 with an error field.
        if "error" in payload and not (payload.get("calendar") or payload.get("best_flights")):
            LOG.warning("searchapi soft-error: %s", payload.get("error"))
        return payload


# --- parsing helpers (top-level for unit-testability) -----------------------


def _parse_calendar_entries(payload: dict) -> list[CalendarEntry]:
    out: list[CalendarEntry] = []
    for raw in payload.get("calendar", []) or []:
        if not isinstance(raw, dict):
            continue
        dep = raw.get("departure")
        ret = raw.get("return")
        price = raw.get("price")
        if not dep or not ret or not isinstance(price, int):
            continue
        if bool(raw.get("has_no_flights", False)) or price <= 0:
            # has_no_flights cells sometimes report a 0 price; either way
            # they carry no signal.
            continue
        out.append(CalendarEntry(
            departure_date=str(dep),
            return_date=str(ret),
            price=int(price),
            has_no_flights=bool(raw.get("has_no_flights", False)),
            is_lowest_price=bool(raw.get("is_lowest_price", False)),
        ))
    return out


def _parse_best_flights(payload: dict) -> list[FlightOption]:
    out: list[FlightOption] = []
    for raw in payload.get("best_flights", []) or []:
        if not isinstance(raw, dict):
            continue
        price = raw.get("price")
        if not isinstance(price, int):
            continue
        segments = raw.get("flights") or []
        carriers = _carriers_from_segments(segments)
        stops = max(0, len(segments) - 1)  # n-1 layovers for n segments
        total_minutes = raw.get("total_duration")
        if not isinstance(total_minutes, int):
            total_minutes = None
        out.append(FlightOption(
            price=int(price),
            total_minutes=total_minutes,
            stops=stops,
            carriers=carriers,
        ))
    return out


def _carriers_from_segments(segments: list[Any]) -> str:
    """Distinct, order-preserving join of segment airlines, e.g. "KLM + Kenya Airways"."""
    names: list[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        name = seg.get("airline")
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return " + ".join(names) if names else "unknown"


def _days_inclusive(start: date, end: date) -> int:
    if end < start:
        return 0
    return (end - start).days + 1
