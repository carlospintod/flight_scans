"""Google Flights direct client — no API key, no quota.

Fetches Google Flights result pages with a local headless Chromium
(playwright), pre-setting the EU consent cookies, and parses the fully
rendered DOM's aria-labels. Each result row carries a natural-language
label with everything we track:

    "From 597 euros round trip total. 1 stop flight with Etihad.
     Leaves ... Total duration 13 hr 40 min. Layover ..."

Validated 2026-07-05 (probe_googleflights_playwright.py): 22 itineraries
parsed for MAD-NBO Sep 15/Nov 15 with prices, carriers, stops, durations.

Why not fast-flights' own fetch: as of v3.0.2 its static-HTML path gets
an empty data payload for this corridor (Google renders results via
async JS), and its only built-in fallbacks are paid integrations. We
reuse fast-flights ONLY for building the protobuf `tfs=` URL.

Cost model: free. ~5-8s per query (page render). Politeness: keep scans
to tens of queries, not hundreds — this is a residential-IP scraper and
Google can serve captchas if hammered.

Availability: requires playwright + a downloaded Chromium. On hosts
without it (e.g. Streamlit Cloud), `is_available()` returns False and
the UI skips this source with a warning — same graceful-skip pattern
as the keyed clients.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from .searchapi_io import FlightOption, PointResponse

LOG = logging.getLogger(__name__)

SOURCE_ID = "googleflights"
PAGE_TIMEOUT_MS = 60_000
RESULTS_SETTLE_MS = 30_000

_CONSENT_COOKIES = [
    {"name": "CONSENT", "value": "YES+cb.20240101-01-p0.en+FX+700",
     "domain": ".google.com", "path": "/"},
    {"name": "SOCS",
     "value": "CAESHAgBEhJnd3NfMjAyNDAxMDEtMF9SQzIaAmVuIAEaBgiA_LyaBg",
     "domain": ".google.com", "path": "/"},
]
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class GoogleFlightsError(RuntimeError):
    pass


class GoogleFlightsUnavailable(GoogleFlightsError):
    """The browser itself can't start (missing executable, dead display).

    Distinct from a per-query failure: retrying other itineraries with the
    same client is pointless, so callers should stop the whole batch."""


def is_available() -> bool:
    """True when playwright + fast-flights are importable AND a Chromium
    executable is installed. Cheap enough to call at client-construction
    time; used for the graceful skip on hosts like Streamlit Cloud."""
    try:
        import fast_flights  # noqa: F401
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        from pathlib import Path
        with sync_playwright() as p:
            # executable_path is the EXPECTED path — it's non-empty even
            # when the browser was never downloaded (observed 2026-07-06:
            # a scan planned 25 free queries and every one failed with
            # "Executable doesn't exist"). Check the file is really there.
            exe = p.chromium.executable_path
            return bool(exe) and Path(exe).exists()
    except Exception:  # noqa: BLE001
        return False


class GoogleFlightsClient:
    """Point-query client with the same surface as SearchApiClient.

    Reuses one browser instance across queries in a scan (startup is the
    expensive part). Call `close()` when done — `run_all` does this via
    the context-manager protocol.
    """

    source_id = SOURCE_ID  # rows produced by this client get this tag

    def __init__(self, *, headless: bool = True):
        self._headless = headless
        self._pw = None
        self._browser = None
        self._ctx = None
        self._launch_error: str | None = None

    @classmethod
    def from_env(cls, **kwargs) -> "GoogleFlightsClient":
        """Constructor mirroring the keyed clients' interface.

        Raises RuntimeError when the local browser stack is missing, so
        `_make_clients` surfaces the same style of warning as a missing
        API key.
        """
        if not is_available():
            raise RuntimeError(
                "googleflights source needs playwright + Chromium locally "
                "(pip install playwright fast-flights && playwright install "
                "chromium). Not available on Streamlit Cloud."
            )
        return cls(**kwargs)

    # -- browser lifecycle --

    def _ensure_browser(self):
        if self._ctx is not None:
            return
        # Once a launch has failed, every later query would fail the same
        # way — and re-calling sync_playwright().start() after a half-torn
        # first attempt surfaces as the misleading "Sync API inside the
        # asyncio loop" error on all 24 remaining candidates (observed
        # 2026-07-06). Fail fast with the ORIGINAL cause instead.
        if self._launch_error is not None:
            raise GoogleFlightsUnavailable(self._launch_error)
        from playwright.sync_api import sync_playwright
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self._headless)
            self._ctx = self._browser.new_context(locale="en-US", user_agent=_UA)
            self._ctx.add_cookies(_CONSENT_COOKIES)
        except Exception as exc:
            self._launch_error = f"browser launch failed: {exc}"
            self.close()  # stop the half-started playwright driver
            raise GoogleFlightsUnavailable(self._launch_error) from exc

    def close(self) -> None:
        for closer in (
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
        self._pw = self._browser = self._ctx = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- the query --

    def point_query(
        self,
        *,
        origin: str,
        destination: str,
        outbound: date,
        return_: date,
        currency: str = "EUR",
        adults: int = 1,
    ) -> PointResponse:
        """Fetch + parse one round-trip result page. Free; ~5-8s."""
        from fast_flights import FlightQuery, Passengers, create_query

        q = create_query(
            flights=[
                FlightQuery(date=outbound.isoformat(),
                            from_airport=origin, to_airport=destination),
                FlightQuery(date=return_.isoformat(),
                            from_airport=destination, to_airport=origin),
            ],
            trip="round-trip", seat="economy",
            passengers=Passengers(adults=adults),
            currency=currency, language="en-US",
        )
        url = q.url()
        LOG.info("googleflights GET %s->%s dep=%s ret=%s",
                 origin, destination, outbound, return_)
        self._ensure_browser()
        page = self._ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded",
                      timeout=PAGE_TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle",
                                         timeout=RESULTS_SETTLE_MS)
            except Exception:  # noqa: BLE001 — settle timeout is fine, parse what's there
                pass
            html = page.content()
        finally:
            page.close()

        options = _parse_aria_labels(html)
        if not options and "consent.google.com" in html[:3000]:
            raise GoogleFlightsError("hit the Google consent page — cookie bypass failed")
        return PointResponse(raw={"html_chars": len(html)},
                             best_flights=tuple(options))


# --- parser (pure, fixture-testable) ----------------------------------------

_LABEL_RE = re.compile(r'aria-label="(From \d[^"]*?euros[^"]*?)"')
_PRICE_RE = re.compile(r"From (\d[\d,]*) euros")
_STOPS_RE = re.compile(r"(Nonstop|\d+ stops?) flight")
_CARRIER_RE = re.compile(r"flight with ([^.]+)\.")
_DURATION_RE = re.compile(r"Total duration (?:(\d+) hr)?\s*(?:(\d+) min)?")


def _parse_aria_labels(html: str) -> list[FlightOption]:
    """Extract flight options from a rendered Google Flights page.

    Anchors on the result rows' aria-labels, which are stable natural-
    language sentences (unlike the obfuscated CSS class names). Returns
    options sorted by price ascending, deduplicated on
    (price, carriers, duration).
    """
    out: list[FlightOption] = []
    seen: set[tuple] = set()
    for m in _LABEL_RE.finditer(html):
        label = m.group(1)
        pm = _PRICE_RE.search(label)
        if not pm:
            continue
        price = int(pm.group(1).replace(",", ""))

        sm = _STOPS_RE.search(label)
        if sm:
            stops = 0 if sm.group(1) == "Nonstop" else int(sm.group(1).split()[0])
        else:
            stops = -1  # unknown

        cm = _CARRIER_RE.search(label)
        carriers = "unknown"
        if cm:
            # "Etihad" / "Brussels Airlines" / "Iberia and Kenya Airways"
            carriers = " + ".join(
                part.strip() for part in re.split(r",| and ", cm.group(1))
                if part.strip()
            )

        dm = _DURATION_RE.search(label)
        total_minutes = None
        if dm and (dm.group(1) or dm.group(2)):
            total_minutes = int(dm.group(1) or 0) * 60 + int(dm.group(2) or 0)

        key = (price, carriers, total_minutes)
        if key in seen:
            continue
        seen.add(key)
        out.append(FlightOption(
            price=price,
            total_minutes=total_minutes,
            stops=max(stops, 0),
            carriers=carriers,
        ))
    out.sort(key=lambda f: f.price)
    return out
