"""Google Travel Explore — origin→anywhere discovery on Google's own data.

Why this exists (D1 amendment, 2026-08-09). Discovery was 100% the
Travelpayouts cache, and the cache lies about exactly the fares the
product sells:

    cached BCN->SSH 110  ->  live 241   (+119%)
    cached MAD->NYC 427  ->  Google returns nothing for the pair
    cached long-haul rows arrive as METRO codes (NYC, LON, TYO) that
    Google Flights rejects outright

Explore is the same corpus we verify against, so a candidate it
nominates is a candidate that can actually be confirmed. Measured on
one live call, MAD + North America + November:

    EWR 398  YYZ 429  ORD 448  IAD 467  LAX 478  SFO 487

— real airport codes, real (start_date, end_date) pairs, Google prices.

ONE ENGINE, TWO VENDORS. SerpAPI and SearchAPI both expose it as
`google_travel_explore` with the same parameter names, so this adapter
takes the vendor as config. Vuelazo runs it on SerpAPI's free 250/mo
today and flips to SearchAPI Developer by changing `provider` — no
parsing changes (D1 amendment: SearchAPI is the chosen paid rail).

UNDIRECTED CALLS ARE A TRAP. `departure_id` alone returns Google's
default list, which is Europe-heavy: 66 destinations, 45 priced,
nothing above EUR 325 — the same intra-EU bias that made the cached
sweep useless for long-haul. Long-haul needs `arrival_area_id`, a
continent kgmid. AREAS below is that list.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date

import requests

from .credentials import redact_network_error
from .deals_db import Observation

LOG = logging.getLogger(__name__)

# Continent kgmids for arrival_area_id. Europe is deliberately ABSENT:
# the free Aviasales sweep already covers intra-EU breadth at zero cost
# and zero quota, so paying a Google call for it buys nothing.
AREAS: dict[str, str] = {
    "north_america": "/m/059g4",
    "south_america": "/m/06n3y",
    "asia": "/m/0j0k",
    "africa": "/m/0dg3n1",
    # Australia, not the Oceania landmass id: /m/05h0n returns "Empty
    # results" for every month tested, while /m/0chghy returns SYD, MEL,
    # BNE, PER — and Australia is where the sellable Oceania fares are.
    # Every id here is probed live before it ships (test_explore.py).
    "oceania": "/m/0chghy",
}

# SerpAPI reports "no destinations for this window" as an ERROR payload,
# not an empty 200. Measured: asia/October is empty from MAD while
# asia/November and asia/December both return 30+ destinations — so an
# empty window is ordinary data, not a fault. Treating it as an error
# made a normal result look identical to a broken area id.
_EMPTY_RE = re.compile(r"empty results", re.I)

PROVIDERS = {
    "serpapi": ("https://serpapi.com/search", "SERPAPI_KEY_VZ"),
    "searchapi": ("https://www.searchapi.io/api/v1/search", "SEARCHAPI_KEY_VZ"),
}


class ExploreError(RuntimeError):
    """Explore call failed. Discovery degrades per call, never aborts."""


@dataclass(frozen=True)
class ExploreQuote:
    origin: str
    dest: str                 # AIRPORT code (EWR), never a metro code
    depart_date: str
    return_date: str | None
    price: int
    currency: str
    airline: str | None
    stops: int | None


class ExploreClient:
    """`google_travel_explore` on either vendor. One call = one
    (origin, area, month) window; the response is a destination list."""

    source_id = "explore"

    def __init__(self, api_key: str, *, provider: str = "serpapi",
                 timeout_s: int = 120):
        if provider not in PROVIDERS:
            raise ExploreError(f"unknown Explore provider {provider!r}")
        self._key = api_key
        self._provider = provider
        self._url = PROVIDERS[provider][0]
        self._timeout_s = timeout_s
        self._session = requests.Session()

    @classmethod
    def from_env(cls, var: str | None = None, *, provider: str = "serpapi",
                 **kwargs) -> "ExploreClient":
        var = var or PROVIDERS[provider][1]
        key = os.environ.get(var, "").strip()
        if not key:
            raise RuntimeError(
                f"{var} is not set — Explore discovery needs Vuelazo's own "
                f"{provider} key.")
        return cls(key, provider=provider, **kwargs)

    def explore(self, *, origin: str, month: str, area: str | None = None,
                currency: str = "EUR") -> list[ExploreQuote]:
        """One metered call. `month` is 'YYYY-MM' (only the month number
        reaches the API); `area` is a key of AREAS, or None for Google's
        undirected — and Europe-biased — default list."""
        params = {
            "engine": "google_travel_explore",
            "api_key": self._key,
            "departure_id": origin.upper(),
            "month": str(int(month.split("-")[1])),
            "currency": currency,
            "hl": "en",
            "gl": "es",
        }
        if area:
            if area not in AREAS:
                raise ExploreError(f"unknown area {area!r}")
            params["arrival_area_id"] = AREAS[area]
        try:
            r = self._session.get(self._url, params=params,
                                  timeout=self._timeout_s)
        except requests.RequestException as exc:
            # str(exc) holds the full URL, and both vendors take the key
            # as the api_key query param (CLAUDE.md #6).
            raise ExploreError(redact_network_error(exc, self._key)) from None
        try:
            data = r.json()
        except ValueError as exc:
            raise ExploreError("non-JSON response") from exc
        if not r.ok or "error" in data:
            msg = str(data.get("error", r.text[:200]))
            if _EMPTY_RE.search(msg):
                # Ordinary "nothing here this month", not a fault. The
                # credit is still spent and still recorded; the caller
                # gets an empty list instead of an exception.
                LOG.info("explore %s/%s %s: no destinations this window",
                         origin, params.get("arrival_area_id", "-"),
                         params["month"])
                return []
            raise ExploreError(msg)
        return parse_explore(data, origin=origin, currency=currency)


def parse_explore(payload: dict, *, origin: str,
                  currency: str = "EUR") -> list[ExploreQuote]:
    """Defensive parse (CLAUDE.md #5). Measured on a real response: 21
    of 66 destination rows carry NO price, airport code or dates — they
    are the cards Google renders without a fare. Skipping them silently
    is correct; a row without a price is not an observation."""
    out: list[ExploreQuote] = []
    for row in payload.get("destinations") or []:
        if not isinstance(row, dict):
            continue
        code = ((row.get("destination_airport") or {}).get("code") or "").upper()
        price = row.get("flight_price")
        dep = row.get("start_date")
        if not code or not isinstance(price, int) or not dep:
            continue
        stops = row.get("number_of_stops")
        out.append(ExploreQuote(
            origin=origin.upper(), dest=code, depart_date=str(dep),
            return_date=str(row["end_date"]) if row.get("end_date") else None,
            price=price, currency=currency,
            airline=row.get("airline") or None,
            stops=stops if isinstance(stops, int) else None,
        ))
    return out


def to_observations(quotes: list[ExploreQuote]) -> list[Observation]:
    """Explore rows are Google-family and LIVE-ish, but they are still
    nominations, not verifications: `is_verified` stays False so the
    "no alert without live verification" rule is untouched. The family
    is `google` so lib/confidence.py cannot count Explore and the
    verification as two independent views of the market."""
    return [Observation(
        origin=q.origin, dest=q.dest, depart_date=q.depart_date,
        return_date=q.return_date, price=q.price, currency=q.currency,
        source="explore", source_family="google",
        found_at=None, is_verified=False) for q in quotes]


def rotation_plan(origins: list[str], areas: list[str], months: list[str],
                  *, day: date, budget: int) -> list[tuple[str, str, str]]:
    """The `budget` (origin, area, month) windows to sweep TODAY.

    Explore's full grid is len(origins) x len(areas) x len(months) — 120
    windows for 4 origins, 5 areas, 6 months. At the free tier's ~4
    calls/day that is a 30-day cycle, so the grid is walked in a fixed
    rotation keyed to the day-of-year: every window is visited on a
    predictable cadence and none is ever starved. Deterministic, so a
    re-run on the same day re-sweeps the same windows rather than
    drifting through the grid and double-spending.
    """
    grid = [(o, a, m) for m in months for a in areas for o in origins]
    if not grid or budget <= 0:
        return []
    start = (day.toordinal() * budget) % len(grid)
    return [grid[(start + i) % len(grid)] for i in range(min(budget, len(grid)))]


@dataclass
class CollectResult:
    """What one day's Explore rotation actually produced.

    `stored` counts rows that reached fare_observations; `unpersisted`
    holds rows a failed insert could not store (the full pipeline can
    still gate on them this run; the passive collector cannot use them).
    """
    windows: int = 0
    stored: int = 0
    failed: int = 0
    empty: int = 0
    errors: list[str] = field(default_factory=list)
    persisted: list[Observation] = field(default_factory=list)
    unpersisted: list[Observation] = field(default_factory=list)


def collect_windows(client, windows: list[tuple[str, str, str]], conn, *,
                    currency: str = "EUR",
                    errors: tuple[type[BaseException], ...] = ()) -> CollectResult:
    """Run the Explore calls for `windows` and persist each window's rows
    as soon as it returns. `client` is the GuardedClient (the ledger
    charges before every call). Per-window failures are counted, never
    raised; `errors` are the extra exception types to treat as a failed
    window (the caller passes QuotaExceeded)."""
    from .deals_db import insert_observations

    res = CollectResult(windows=len(windows))
    for origin, area, month in windows:
        try:
            quotes = client.explore(origin=origin, month=month, area=area,
                                    currency=currency)
        except (ExploreError, *errors) as exc:
            LOG.warning("explore %s/%s/%s failed: %s", origin, area, month, exc)
            res.failed += 1
            res.errors.append(f"{origin}/{area}/{month}: {exc}")
            continue
        if not quotes:
            res.empty += 1
            continue
        # Persist per window: Explore is the metered call, so a crash
        # later in the run must not throw away rows already paid for.
        window_obs = to_observations(quotes)
        try:
            insert_observations(conn, window_obs)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("explore %s/%s/%s: rows not persisted (%s)",
                        origin, area, month, exc)
            res.errors.append(f"{origin}/{area}/{month}: not stored ({exc})")
            res.unpersisted.extend(window_obs)
        else:
            res.persisted.extend(window_obs)
            res.stored += len(window_obs)
    return res


def collect_verdict(res: CollectResult) -> tuple[str, str]:
    """('ok' | 'warning' | 'error', one-line reason) for a rotation.

    A run that stores NOTHING is an error even when every call "worked":
    a collector that runs green while writing zero rows is exactly the
    silent failure the passive mode exists to rule out. Some failed or
    empty windows are a warning, not a page — asia/October really is
    empty from MAD."""
    if res.windows == 0:
        return "ok", "no Explore window due today (already collected)"
    if res.stored == 0:
        return "error", (f"0 Explore rows stored from {res.windows} window(s): "
                         f"{res.failed} failed, {res.empty} empty"
                         + (f" — last error: {res.errors[-1]}" if res.errors else ""))
    if res.failed or res.unpersisted:
        return "warning", (f"{res.stored} rows stored; {res.failed} of "
                           f"{res.windows} window(s) failed"
                           + (f" — last error: {res.errors[-1]}" if res.errors else ""))
    return "ok", (f"{res.stored} rows stored from {res.windows} window(s)"
                  + (f" ({res.empty} empty)" if res.empty else ""))


def windows_due_today(origins: list[str], areas: list[str], months: list[str],
                      *, day: date, per_day: int, done_today: int
                      ) -> list[tuple[str, str, str]]:
    """Today's rotation minus the calls already made today.

    The rotation is planned for the FULL daily budget and then sliced, so
    a same-day re-run continues today's windows instead of jumping to a
    different part of the grid (planning with the reduced budget shifted
    the rotation's start)."""
    plan = rotation_plan(origins, areas, months, day=day, budget=per_day)
    return plan[max(0, done_today):]
