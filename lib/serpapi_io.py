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

`calendar()` (2026-11-14) is the full-window rail: the
google_flights_calendar engine prices EVERY departure day of a range for
one trip length per call, so the tracker's 4,030 date pairs cost one call
per (origin, stay length). See lib/sweep.py for the strip planner.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

from .searchapi_io import (
    CalendarEntry,
    CalendarResponse,
    PointResponse,
    _parse_best_flights,
)

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SellerOption:
    """One booking option Google Flights lists for an itinerary — the
    actual seller (an airline or an OTA like Gotogate/Mytrip/Trip.com)
    and its price. The cheapest of these is often BELOW the headline
    price shown on best_flights, which is the OTA-coverage the roster
    was missing (2026-07-13 audit)."""
    book_with: str
    price: int
    currency: str
    separate_tickets: bool

BASE_URL = "https://serpapi.com/search"
ACCOUNT_URL = "https://serpapi.com/account"
# GET /account echoes the account's own api_key, email and id next to the
# plan counters, and callers store check_quota()["raw"] verbatim in
# quota_snapshots.raw_json. Only these fields leave the adapter (CLAUDE.md
# #6). It is an allowlist, so a field SerpAPI adds later stays out until
# someone decides it is safe. scripts/scrub_quota_secrets.py cuts rows
# stored before 2026-11-14 down to the same subset.
ACCOUNT_SAFE_FIELDS = (
    "plan_name",
    "plan_searches_left",
    "searches_per_month",
    "total_searches_left",
    "extra_credits",
    "this_month_usage",
    "this_hour_searches",
    "account_rate_limit_per_hour",
    "account_status",        # tells a suspended account from an empty one
    "plan_renewal_date",     # explains a pool that jumps back up
)
DEFAULT_TIMEOUT_S = 90
# A calendar sweep makes ~60 calls in one run; at the point-query timeout
# a bad day could hold the CI job for 90 minutes. 60s bounds each strip,
# and the sweep's own wall budget (lib/sweep.py) bounds the whole pass.
CALENDAR_TIMEOUT_S = 60
SOURCE_ID = "serpapi"
# Row label for calendar-sweep cells in calendar_snapshots. The LEDGER
# source is still "serpapi" (same account, same pool); the separate label
# keeps grid cells apart from point-query prices, because they mean
# different things: a cell is Google's calendar price (a nomination the
# free scraper then verifies), a point query is a priced itinerary.
# lib/alerts.py does not alert on this label — two sweeps two weeks apart
# would otherwise compare ~4,000 cells and fire hundreds of new_low rows.
CALENDAR_SOURCE_ID = "serpapi_calendar"

# SerpAPI answers "Google found nothing for this strip" with an error
# payload. For a calendar strip that is ordinary data (no flights that
# week), so it becomes an empty response, not an exception.
_NO_RESULTS_RE = re.compile(r"hasn.t returned any results|empty results|no results",
                            re.I)


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

    def booking_options(
        self,
        *,
        booking_token: str,
        origin: str,
        destination: str,
        outbound: date,
        return_: date | None,
        currency: str,
        adults: int = 1,
    ) -> list[SellerOption]:
        """The sellers Google lists for one itinerary — airlines AND OTAs
        (Gotogate/Mytrip/Trip.com/Kiwi). The cheapest is often an OTA
        below the best_flights headline price; capturing it is the free
        OTA-coverage win (no new source). `booking_token` comes from a
        prior point_query's best_flights[i]['booking_token']."""
        params: dict[str, Any] = {
            "engine": "google_flights",
            "api_key": self._api_key,
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": outbound.isoformat(),
            "type": "1" if return_ is not None else "2",
            "currency": currency,
            "adults": adults,
            "hl": "en",
            "booking_token": booking_token,
        }
        if return_ is not None:
            params["return_date"] = return_.isoformat()
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
        return _parse_booking_options(data, currency)

    def calendar(
        self,
        *,
        origin: str,
        destination: str,
        stay_days: int,
        outbound_start: date,
        outbound_end: date,
        currency: str,
        adults: int = 1,
        extra: dict[str, Any] | None = None,
    ) -> CalendarResponse:
        """One stay-length STRIP of the google_flights_calendar engine:
        Google's round-trip price for every departure day in
        [outbound_start, outbound_end], each returning `stay_days` later.

        One call = one credit (metered as `calendar` in lib/sources.py).
        The whole NBO window is one strip per stay length per origin,
        so 31 stays x 2 origins = 62 calls price every date pair.

        Returns the same CalendarResponse/CalendarEntry shape as the
        SearchAPI calendar, so the sweep stores both the same way. A
        "no results" error payload is an empty response (still charged).
        """
        if stay_days < 1:
            raise SerpApiError(400, f"stay_days must be >= 1 (got {stay_days})")
        if outbound_end < outbound_start:
            raise SerpApiError(
                400, f"empty departure range {outbound_start}..{outbound_end}")
        params: dict[str, Any] = {
            "engine": "google_flights_calendar",
            "api_key": self._api_key,
            "departure_id": origin,
            "arrival_id": destination,
            "type": "1",                       # round trip
            "date_range_start": outbound_start.isoformat(),
            "date_range_end": outbound_end.isoformat(),
            "round_trip_days": stay_days,
            "currency": currency,
            "adults": adults,
            "hl": "en",
        }
        if extra:
            params.update(extra)
        LOG.info("serpapi calendar %s->%s stay=%d dep=%s..%s",
                 origin, destination, stay_days, outbound_start, outbound_end)
        try:
            r = self._session.get(BASE_URL, params=params,
                                  timeout=CALENDAR_TIMEOUT_S)
        except requests.RequestException as exc:
            raise SerpApiError(0, f"network error: {exc}") from exc
        try:
            data = r.json()
        except ValueError as exc:
            raise SerpApiError(r.status_code, "non-JSON response") from exc
        if not isinstance(data, dict):
            raise SerpApiError(r.status_code, "response body was not a JSON object")
        if not r.ok or "error" in data:
            msg = str(data.get("error", r.text[:200]))
            if r.ok and _NO_RESULTS_RE.search(msg):
                LOG.info("serpapi calendar %s->%s stay=%d: no results (%s)",
                         origin, destination, stay_days, msg)
                return CalendarResponse(raw=data, entries=())
            raise SerpApiError(r.status_code, msg, payload=data)
        return CalendarResponse(raw=data,
                                entries=tuple(_parse_calendar_prices(data)))

    def check_quota(self) -> dict:
        """Return the current SerpAPI quota (does not count as a search).

        GET /account returns e.g.:
            {"plan_searches_left": 93, "searches_per_month": 100,
             "this_month_usage": 7, ...}
        plus the account's api_key, email and id. Normalized to
        {remaining, limit_total, raw} like the other clients. `raw` holds
        only ACCOUNT_SAFE_FIELDS, because callers store it verbatim.
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
        if not isinstance(data, dict):
            raise SerpApiError(r.status_code, "/account response was not a JSON object")
        remaining = data.get("plan_searches_left")
        # total_searches_left includes purchased top-ups; plan_searches_left
        # is the renewing monthly pool we budget against.
        limit_total = data.get("searches_per_month")
        return {
            "remaining": remaining if isinstance(remaining, int) else None,
            "limit_total": limit_total if isinstance(limit_total, int) else None,
            "raw": safe_account_fields(data),
        }


def safe_account_fields(payload: Any) -> dict:
    """The part of a /account payload that is safe to store: the
    ACCOUNT_SAFE_FIELDS it carries, and nothing else."""
    if not isinstance(payload, dict):
        return {}
    return {k: payload[k] for k in ACCOUNT_SAFE_FIELDS if k in payload}


def _parse_point(payload: dict) -> list:
    """best_flights, falling back to other_flights (same documented shape).

    SerpAPI omits best_flights entirely when Google doesn't rank a "best"
    group for the query — the results are all in other_flights then.
    """
    options = _parse_best_flights(payload)
    if not options and payload.get("other_flights"):
        options = _parse_best_flights({"best_flights": payload["other_flights"]})
    return options


def _parse_calendar_prices(payload: dict) -> list[CalendarEntry]:
    """google_flights_calendar `prices[]` -> CalendarEntry (defensive:
    CLAUDE.md #5). Documented cell: {outbound_date, return_date, price,
    low_price}. A cell without both ISO dates or a positive numeric price
    is not an observation and is dropped. `low_price` ("within 5% of the
    range minimum") maps to is_lowest_price, Google's own cheap-cell flag.
    """
    out: list[CalendarEntry] = []
    for raw in payload.get("prices") or []:
        if not isinstance(raw, dict):
            continue
        dep, ret, price = (raw.get("outbound_date"), raw.get("return_date"),
                           raw.get("price"))
        if not isinstance(dep, str) or not isinstance(ret, str):
            continue
        try:
            date.fromisoformat(dep)
            date.fromisoformat(ret)
        except ValueError:
            continue
        # bool is an int subclass: True must not become a 1-euro fare.
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            continue
        if price <= 0:
            continue
        out.append(CalendarEntry(
            departure_date=dep, return_date=ret, price=int(round(price)),
            has_no_flights=False,
            is_lowest_price=bool(raw.get("low_price", False)),
        ))
    return out


def top_booking_token(resp: PointResponse) -> str | None:
    """The booking_token of the cheapest itinerary in a point_query
    response — feed it to booking_options() to fetch its sellers."""
    best = (resp.raw or {}).get("best_flights") or (resp.raw or {}).get("other_flights")
    if best and isinstance(best[0], dict):
        tok = best[0].get("booking_token")
        return str(tok) if tok else None
    return None


def _parse_booking_options(payload: dict, currency: str) -> list[SellerOption]:
    """SerpAPI booking_options -> sellers. Each option is `together`
    (one booking) or `departing`+`returning` (separate tickets, prices
    summed). `book_with` is the seller (airline or OTA)."""
    out: list[SellerOption] = []
    for opt in payload.get("booking_options") or []:
        if not isinstance(opt, dict):
            continue
        together = opt.get("together")
        if isinstance(together, dict):
            block, separate = together, bool(together.get("separate_tickets"))
            price = block.get("price")
        else:  # separate departing + returning tickets
            dep, ret = opt.get("departing") or {}, opt.get("returning") or {}
            block, separate = (dep or ret), True
            dp, rp = dep.get("price"), ret.get("price")
            price = ((dp or 0) + (rp or 0)) if (dp or rp) else None
        book_with = block.get("book_with") if isinstance(block, dict) else None
        if book_with and isinstance(price, (int, float)) and price > 0:
            out.append(SellerOption(
                book_with=str(book_with), price=int(round(price)),
                currency=currency.upper(), separate_tickets=separate))
    out.sort(key=lambda s: s.price)
    return out
