"""Vuelazo pipeline steps (MVP-SPEC §3), M0 slice.

Pure-ish functions the runner (run_deals.py) chains:
  sweep_origin      — cached anywhere discovery -> Observations (nominate only)
  verify_candidate  — ONE Google-family live check per surviving candidate
  price_insights    — Google's typical-range where it populates (D2 cold start)
  baseline_context  — the honest "precio normal" line for the draft (data-
                      sourced, never invented; says WHERE the number comes from)
  booking_url       — direct deep link (D9: no affiliate wrappers)
  deal_confidence   — lib/confidence.py semantics over the sources that
                      actually produced data for THIS deal

Clients arrive already wrapped in GuardedClients — every metered call in
here is charged before it happens.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import SimpleNamespace
from urllib.parse import quote

from .confidence import ConfidenceResult, assess_confidence
from .dealconfig import DealConfig
from .dealgate import Candidate
from .deals_db import Observation

LOG = logging.getLogger(__name__)


# -- 1. Discover (free, cached — nominates, never alerts) -------------------

def sweep_months(today: date, months_ahead: int) -> list[str]:
    """["YYYY-MM", ...] for the current month plus `months_ahead - 1`."""
    out = []
    y, m = today.year, today.month
    for _ in range(max(1, months_ahead)):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def sweep_origin(aviasales, *, origin: str, months: list[str],
                 currency: str) -> list[Observation]:
    """Origin-only anywhere sweep: one metered aviasales call per month.
    Failures of a single month degrade (logged), they don't abort."""
    obs: list[Observation] = []
    for month in months:
        try:
            resp = aviasales.anywhere_prices(origin=origin, month=month,
                                             currency=currency)
        except Exception as exc:  # noqa: BLE001 — degrade per month, loudly
            LOG.warning("sweep %s %s failed: %s", origin, month, exc)
            continue
        for q in resp.quotes:
            if q.origin.upper() != origin.upper():
                continue  # defensive: cross-origin rows out of a cached mirror
            obs.append(Observation(
                origin=q.origin.upper(), dest=q.destination.upper(),
                depart_date=q.departure_date, return_date=q.return_date,
                price=q.price, currency=q.currency,
                source="aviasales", source_family="cached",
                found_at=q.found_at, is_verified=False,
            ))
    return obs


def watchlist_refresh(aviasales, *, routes: list[tuple[str, str]],
                      currency: str) -> list[Observation]:
    """Daily watchlist refresh (D1): one cached latest_prices call per
    (origin, dest). Broadens coverage beyond the sweep's top-100-by-price
    horizon; failures degrade per route."""
    obs: list[Observation] = []
    for origin, dest in routes:
        try:
            resp = aviasales.latest_prices(origin=origin, destination=dest,
                                           currency=currency, limit=30)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("watchlist %s->%s failed: %s", origin, dest, exc)
            continue
        for q in resp.quotes:
            obs.append(Observation(
                origin=q.origin.upper(), dest=q.destination.upper(),
                depart_date=q.departure_date, return_date=q.return_date,
                price=q.price, currency=q.currency,
                source="aviasales", source_family="cached",
                found_at=q.found_at, is_verified=False,
            ))
    return obs


# -- 3. Verify (paid, ledger-reserved) --------------------------------------

@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    live_price: int | None
    carriers: str | None
    insights: dict | None      # parsed price_insights, when Google has them
    note: str


def parse_price_insights(raw: dict) -> dict | None:
    """Defensive extraction of SerpAPI's price_insights block:
    {"lowest_price": int, "price_level": str,
     "typical_price_range": [lo, hi]} — any part may be absent."""
    pi = (raw or {}).get("price_insights")
    if not isinstance(pi, dict):
        return None
    out: dict = {}
    lo_hi = pi.get("typical_price_range")
    if (isinstance(lo_hi, (list, tuple)) and len(lo_hi) == 2
            and all(isinstance(x, (int, float)) for x in lo_hi)):
        out["typical_low"], out["typical_high"] = int(lo_hi[0]), int(lo_hi[1])
    if isinstance(pi.get("lowest_price"), (int, float)):
        out["lowest_price"] = int(pi["lowest_price"])
    if isinstance(pi.get("price_level"), str):
        out["price_level"] = pi["price_level"]
    return out or None


def verify_candidate(serpapi, cand: Candidate,
                     config: DealConfig) -> VerifyResult:
    """One live Google-family confirmation. The cached price nominated;
    only the LIVE price publishes. Fails quietly (ok=False + note)."""
    try:
        dep = date.fromisoformat(cand.depart_date)
        ret = date.fromisoformat(cand.return_date) if cand.return_date else None
    except ValueError as exc:
        return VerifyResult(False, None, None, None, f"bad dates: {exc}")
    try:
        resp = serpapi.point_query(
            origin=cand.origin, destination=cand.dest,
            outbound=dep, return_=ret, currency=config.currency)
    except Exception as exc:  # noqa: BLE001 — verification failures die quietly
        return VerifyResult(False, None, None, None, f"serpapi error: {exc}")
    options = resp.best_flights
    if not options:
        return VerifyResult(False, None, None,
                            parse_price_insights(resp.raw), "no live options")
    best = min(options, key=lambda o: o.price)
    ceiling = cand.price * (1 + config.live_tolerance_pct / 100.0)
    insights = parse_price_insights(resp.raw)
    if best.price > ceiling:
        return VerifyResult(
            False, best.price, best.carriers, insights,
            f"live {best.price} exceeds cached {cand.price} "
            f"+{config.live_tolerance_pct:.0f}% tolerance")
    return VerifyResult(True, best.price, best.carriers, insights,
                        "live-confirmed")


def classify_deal(cand: Candidate, verify: VerifyResult,
                  config: DealConfig) -> str:
    """'mistake' | 'standard', decided AFTER live verification.

    Cold-start crude catch (D2): the live price below
    mistake_pct_of_typical% of Google's route-specific typical LOW.
    Without insights there is no route-specific normal to be wrong
    about — stays standard. (Trailing-P25 form activates with M1
    baselines.)"""
    if not verify.insights or "typical_low" not in verify.insights:
        return "standard"
    price = verify.live_price if verify.live_price is not None else cand.price
    threshold = verify.insights["typical_low"] * config.mistake_pct_of_typical / 100.0
    return "mistake" if price < threshold else "standard"


# -- 4. Score & draft inputs ------------------------------------------------

def baseline_context(cand: Candidate, insights: dict | None
                     ) -> tuple[int | None, str]:
    """(baseline_median_for_db, Spanish context line for the draft).

    Honesty rule (D7): the number ALWAYS names its true source.
    Preference: Google's typical range (route-specific) > our own data —
    which is either this route's trailing verified median (baseline-mode
    candidates) or the same-day class cross-section (cold-start
    candidates). The wording must match the gate that produced the
    number; a class median labeled as route history (or vice versa)
    would be an invented claim on the product surface.
    """
    if insights and "typical_low" in insights:
        lo, hi = insights["typical_low"], insights["typical_high"]
        mid = (lo + hi) // 2
        return mid, (f"el rango tipico de esta ruta segun Google Flights es "
                     f"{lo}-{hi} EUR; hoy esta en {cand.price} EUR")
    if cand.gate_mode == "baseline":
        return cand.baseline_median, (
            f"la mediana de tarifas verificadas de esta ruta en los "
            f"ultimos 60 dias es {cand.baseline_median} EUR; esta esta un "
            f"{cand.pct_below:.0f}% por debajo")
    return cand.xsection_median, (
        f"la mediana hoy de rutas comparables ({cand.route_class}) desde "
        f"{cand.origin} es {cand.xsection_median} EUR; esta esta un "
        f"{cand.pct_below:.0f}% por debajo")


def booking_url(cand: Candidate) -> str:
    """Direct Google Flights deep link — no affiliate wrappers (D9)."""
    q = (f"vuelos de {cand.origin} a {cand.dest} el {cand.depart_date}"
         + (f" vuelta el {cand.return_date}" if cand.return_date else ""))
    return f"https://www.google.com/travel/flights?hl=es&q={quote(q)}"


def draft_fields(cand: Candidate, verify: VerifyResult,
                 baseline_line: str) -> dict:
    """The DATOS block for templates/deal_draft_es.md — every value comes
    from the pipeline, none is invented."""
    price = verify.live_price if verify.live_price is not None else cand.price
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    return {
        "origin": cand.origin,
        "dest": cand.dest,
        "is_round_trip": "si" if cand.return_date else "no (solo ida)",
        "price": price,
        "currency": cand.currency,
        "depart_date": cand.depart_date,
        "return_date": cand.return_date or "(solo ida)",
        "baseline_line": baseline_line,
        "carrier": verify.carriers or "ver en el enlace",
        "deal_class": cand.deal_class,
        "verification_line": (
            f"confirmado en vivo en Google Flights a {price} "
            f"{cand.currency} ({now})"),
        "booking_url": booking_url(cand),
    }


# -- Confidence -------------------------------------------------------------

def deal_confidence(*, cached_produced: bool,
                    live_verified: bool) -> ConfidenceResult:
    """lib/confidence.py semantics over THIS deal's producing sources —
    families, not endpoints. M0's rails: aviasales (cached) + serpapi
    (google). Mistake-class needs >=2 live coverage families; with one,
    the runner downgrades or drops (never publishes on cached-only)."""
    health = {}
    if cached_produced:
        health["aviasales"] = SimpleNamespace(verdict="live")
    if live_verified:
        health["serpapi"] = SimpleNamespace(verdict="live")
    return assess_confidence(health)
