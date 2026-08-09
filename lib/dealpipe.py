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
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from urllib.parse import quote

from .confidence import ConfidenceResult, assess_confidence
from .dealconfig import DealConfig
from .dealgate import Candidate
from .deals_db import Observation

LOG = logging.getLogger(__name__)


# -- 1. Discover (free, cached — nominates, never alerts) -------------------

def watchlist_routes(config) -> list[tuple[str, str, int]]:
    """(origin, dest, months) for every watchlist route, with long-haul
    getting the deeper date coverage — it is the product's reason to
    exist, and its cache is the thinnest."""
    out: list[tuple[str, str, int]] = []
    for route_class, dests in config.watchlist.items():
        months = config.watchlist_months.get(route_class, 1)
        for origin in config.origins:
            for dest in dests:
                if dest != origin:
                    out.append((origin, dest, months))
    return out


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
                 currency: str, sortings: tuple[str, ...] = ("price",),
                 limits: dict[str, int] | None = None) -> list[Observation]:
    """Origin-only anywhere sweep: one metered aviasales call per
    (month, sorting). Two sortings are used because they return
    different populations — "price" the cheapest N (intra-EU heavy),
    "route" breadth across destinations (where long-haul lives).
    Failures of a single call degrade (logged), they don't abort."""
    limits = limits or {}
    obs: list[Observation] = []
    for month in months:
        for sorting in sortings:
            try:
                resp = aviasales.anywhere_prices(
                    origin=origin, month=month, currency=currency,
                    sorting=sorting, limit=limits.get(sorting, 100))
            except Exception as exc:  # noqa: BLE001 — degrade per call
                LOG.warning("sweep %s %s (%s) failed: %s",
                            origin, month, sorting, exc)
                continue
            for q in resp.quotes:
                if q.origin.upper() != origin.upper():
                    continue  # defensive: cross-origin cached mirror rows
                obs.append(Observation(
                    origin=q.origin.upper(), dest=q.destination.upper(),
                    depart_date=q.departure_date, return_date=q.return_date,
                    price=q.price, currency=q.currency,
                    source="aviasales", source_family="cached",
                    found_at=q.found_at, is_verified=False,
                ))
    return obs


def watchlist_refresh(aviasales, *, routes: list[tuple[str, str, int]],
                      currency: str,
                      today: date | None = None) -> list[Observation]:
    """Daily watchlist refresh (D1): cached prices per (origin, dest),
    one call per month of coverage. `routes` is (origin, dest, months).

    Uses prices_for_dates with a MONTH departure — NOT /v2/prices/latest.
    latest_prices echoes return_at == departure_at on round-trip rows,
    which downstream became a same-day round-trip query that Google
    Flights always answers empty: it killed 100% of long-haul
    verifications and left the DB with a single verified observation, so
    no route could ever mature a baseline. prices_for_dates returns real
    (departure, return) pairs. Failures degrade per call."""
    today = today or date.today()
    obs: list[Observation] = []
    for origin, dest, months in routes:
        for month in sweep_months(today, months):
            try:
                resp = aviasales.prices_for_dates(
                    origin=origin, destination=dest, depart_month=month,
                    currency=currency)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("watchlist %s->%s %s failed: %s",
                            origin, dest, month, exc)
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
    # The code actually sent to Google. Differs from the candidate's dest
    # for metro codes (NYC -> JFK): the alert must name the airport the
    # price was proven at, not the city Aviasales nominated.
    airport: str | None = None


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
    # Aviasales speaks metro codes (NYC, LON, TYO); Google Flights does
    # not — it answers "no results" and the candidate dies for a reason
    # that has nothing to do with its price. Substitute the city's
    # gateway airport. See routes/metro_airports.yaml.
    dest = verification_airport(cand.dest, config)
    try:
        resp = serpapi.point_query(
            origin=cand.origin, destination=dest,
            outbound=dep, return_=ret, currency=config.currency)
    except Exception as exc:  # noqa: BLE001 — verification failures die quietly
        return VerifyResult(False, None, None, None, f"serpapi error: {exc}",
                            airport=dest)
    options = resp.best_flights
    if not options:
        return VerifyResult(False, None, None,
                            parse_price_insights(resp.raw), "no live options",
                            airport=dest)
    best = min(options, key=lambda o: o.price)
    ceiling = cand.price * (1 + config.live_tolerance_pct / 100.0)
    insights = parse_price_insights(resp.raw)
    if best.price > ceiling:
        note = (f"live {best.price} exceeds cached {cand.price} "
                f"+{config.live_tolerance_pct:.0f}% tolerance")
        if dest != cand.dest:
            # Worth saying out loud: the cached metro price is the
            # cheapest across ALL the city's airports, so this may be a
            # real fare at another one rather than a stale cache.
            note += (f" (verified at {dest}; cached price was for the "
                     f"{cand.dest} metro area)")
        return VerifyResult(False, best.price, best.carriers, insights, note,
                            airport=dest)
    return VerifyResult(True, best.price, best.carriers, insights,
                        "live-confirmed", airport=dest)


def verification_airport(dest: str, config: DealConfig) -> str:
    """The code to send to Google for a destination — the metro's
    gateway airport when `dest` is a city code, else `dest` itself."""
    return config.metro_airports.get(dest.upper(), dest.upper())


def second_opinion(serpapi, cand: Candidate, first: VerifyResult,
                   config: DealConfig) -> VerifyResult:
    """The PAID read on a candidate the free scraper already confirmed.

    Two jobs, neither of which the scraper can do:
      * price_insights — the route's own typical range, which the
        (now enforced) floor needs;
      * a third opinion on the price. Cached nominated, the scraper
        confirmed; if SerpAPI's read of the same Google corpus diverges
        beyond live_tolerance_pct, one of them is looking at a ghost
        (stale mirror, currency glitch, vanished fare) and the deal must
        NOT publish — "fuentes en desacuerdo" is a verification failure,
        not a rounding note.

    The published price is SerpAPI's: it is the read that carries the
    receipt (insights + carrier) into the alert. If SerpAPI errors or
    returns no options, the free verification stands but the deal
    carries no typical range — the floor becomes unknowable (None) and
    that is recorded, not hidden.
    """
    second = verify_candidate(serpapi, cand, config)
    if second.live_price is None:
        return replace(
            first,
            note=first.note + f"; sin insights (serpapi: {second.note})")
    if first.live_price:
        div = (abs(second.live_price - first.live_price)
               / first.live_price * 100.0)
        if div > config.live_tolerance_pct:
            return VerifyResult(
                False, second.live_price, second.carriers, second.insights,
                f"fuentes en desacuerdo: scraper {first.live_price} vs "
                f"serpapi {second.live_price} ({div:.0f}% > "
                f"{config.live_tolerance_pct:.0f}%)",
                airport=second.airport)
    if not second.ok:
        # A usable price that failed its own tolerance check (live moved
        # above cached) — a real verification failure, keep it.
        return second
    return replace(second,
                   note=f"live-confirmed x2 (scraper {first.live_price}, "
                        f"serpapi {second.live_price})")


def insights_floor_check(cand: Candidate, verify: VerifyResult,
                         config: DealConfig) -> tuple[bool | None, str]:
    """Would this deal clear its ROUTE'S OWN typical price by the class
    floor? Returns (passed, human explanation); passed is None when
    Google gave no typical range for the route.

    This is the honest comparator: a class cross-section median can make
    an ordinary fare look like a bargain (a EUR 37 Turin hop "saving"
    EUR 275 against a Middle-East median). Computed on every
    verification and recorded even when the gate is disabled, so the
    decision to enforce it can be made from data."""
    if not verify.insights or "typical_low" not in verify.insights:
        return None, "sin rango tipico de Google para esta ruta"
    price = verify.live_price if verify.live_price is not None else cand.price
    low = verify.insights["typical_low"]
    floor = config.floors.get(cand.route_class, 0)
    saving = low - price
    return saving >= floor, (
        f"ahorro vs. tipico bajo ({low}) = {saving} EUR, "
        f"floor {cand.route_class} = {floor}")


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
