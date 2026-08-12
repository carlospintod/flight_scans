"""Vuelazo deal gate + cross-route score (DECISIONS D2, day-one form).

Day-one detector (ratified interpretation): absolute route-class floors +
same-day cross-sectional comparison. Per-route percentile gates switch on
per route as history accumulates (M1+, mirroring min_observations); this
module is deliberately the SIMPLE start of that incremental path.

Gate semantics (cold start):
  * The route's cheapest cached price today is compared against the
    cross-sectional MEDIAN of its route class (all routes of that class
    seen in today's sweep).
  * Candidate iff price <= median * (1 - crosssection_pct/100)
    AND (median - price) >= class floor (absolute savings).
  * Mistake-class is classified at VERIFY time (lib/dealpipe) against
    Google's route-specific typical range — a class-wide P25 would
    falsely flag normal ULCC fares as error fares at cold start.

Guardrails (non-negotiable #3, all enforced HERE, day one):
  daily candidate cap · per-route cooldown (unless a further -X%) ·
  dedup by route+price band. Cached-only rows can NOMINATE, never alert.

Cross-route score (deliberately crude, D2): depth below median (%) plus
absolute savings, times the route-class aspiration weight. Carlos's
approve tap is the last mile — the score only orders the queue.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

LOG = logging.getLogger(__name__)

from .dealconfig import EXCLUDED_CLASS, UNCLASSIFIED, DealConfig
from .deals_db import (Observation, active_disproved, deals_created_today,
                       last_deal_for_route)

# A class cross-section needs a few routes for a median to mean anything.
MIN_CLASS_SIZE = 4

# Classes that can never produce a candidate. UNCLASSIFIED used to be
# silently folded into 'medium', which benchmarked short-haul hops
# against Middle-East medians and handed them the 1.3x medium weight —
# the mechanism behind the phantom BCN->TRN "vuelazo". Unknown codes are
# now visible (rejection reason 'unclassified_route') instead of loud.
NON_CANDIDATE_CLASSES = (UNCLASSIFIED, EXCLUDED_CLASS)


def classify_route(dest: str, config: DealConfig) -> str:
    """Route class, or UNCLASSIFIED when the code is unknown. NEVER
    guesses a class: an unknown code with a real floor attached is worse
    than no candidate at all."""
    return config.route_classes.get(dest.upper(), UNCLASSIFIED)


@dataclass(frozen=True)
class Candidate:
    origin: str
    dest: str
    depart_date: str
    return_date: str | None
    price: int
    currency: str
    route_class: str
    xsection_median: int      # class median (crosssection mode) or route
    xsection_p25: int         #   baseline median/P10 (baseline mode)
    pct_below: float          # vs the gate's median
    abs_saving: int           # median - price
    deal_class: str           # 'standard' | 'mistake'
    score: float
    found_at: str | None
    gate_mode: str = "crosssection"   # 'crosssection' | 'baseline'
    baseline_median: int | None = None
    baseline_p10: int | None = None
    rejected_reason: str | None = None   # set when a guardrail killed it


def cheapest_per_route(obs: list[Observation]) -> list[Observation]:
    """The best (lowest) cached quote per (origin, dest)."""
    best: dict[tuple[str, str], Observation] = {}
    for o in obs:
        key = (o.origin, o.dest)
        if key not in best or o.price < best[key].price:
            best[key] = o
    return list(best.values())


def score_candidate(pct_below: float, abs_saving: int, route_class: str,
                    config: DealConfig) -> float:
    weight = config.aspiration_weights.get(route_class, 1.0)
    return round((pct_below + abs_saving / 10.0) * weight, 1)


def gate_candidates(conn, obs: list[Observation], config: DealConfig,
                    *, today: datetime | None = None) -> list[Candidate]:
    """Full gate: cross-section + floors + guardrails. Returns candidates
    ordered by score desc, capped at max_candidates_per_run. Guardrail
    kills are returned too (rejected_reason set) so the runner can log
    WHY nothing surfaced — never silently."""
    now = today or datetime.now(timezone.utc)
    today_prefix = now.strftime("%Y-%m-%d")

    best = cheapest_per_route(obs)

    # Itineraries the live market already disproved (D2 guardrail,
    # 2026-08-12). The cache keeps serving a price verification has
    # shown to be fiction; without this the same six routes are
    # re-nominated 3x a day forever, each costing a daily-cap slot and a
    # paid verification. Dropped BEFORE the cross-section so a phantom
    # price cannot skew its own class median either.
    stale = active_disproved(conn)
    if stale:
        before = len(best)
        best = [o for o in best
                if (o.origin, o.dest, o.depart_date, o.return_date or "")
                not in stale]
        if before != len(best):
            LOG.info("gate: %d itinerary(ies) skipped — disproved live "
                     "within the last %dh", before - len(best),
                     config.disproved_cooldown_hours)

    # Unknown / deliberately-excluded destinations leave the funnel here.
    # They form no cross-section and become no candidate; the caller logs
    # the distinct codes so route_classes.yaml grows from real data.
    unknown_codes = sorted({o.dest for o in best
                            if classify_route(o.dest, config) == UNCLASSIFIED})
    if unknown_codes:
        LOG.info("gate: %d unclassified destination(s) skipped: %s",
                 len(unknown_codes), ", ".join(unknown_codes[:20]))
    best = [o for o in best
            if classify_route(o.dest, config) not in NON_CANDIDATE_CLASSES]

    # Per-route percentile gate first (D2 mature state) — activates the
    # moment a route's verified history is thick enough; everything else
    # falls through to the day-one cross-section.
    from .baselines import route_baseline
    out: list[Candidate] = []
    immature: list[Observation] = []
    for o in best:
        bl = route_baseline(conn, origin=o.origin, dest=o.dest,
                            window_days=config.baseline_window_days,
                            min_observations=config.min_observations,
                            now=now)
        if bl is None:
            immature.append(o)
            continue
        route_class = classify_route(o.dest, config)
        floor = config.floors[route_class]
        saving = bl.median - o.price
        pct_below = 100.0 * saving / bl.median if bl.median else 0.0
        if (o.price > bl.p10 or pct_below < config.crosssection_pct
                or saving < floor):
            continue
        out.append(Candidate(
            origin=o.origin, dest=o.dest,
            depart_date=o.depart_date, return_date=o.return_date,
            price=o.price, currency=o.currency, route_class=route_class,
            xsection_median=bl.median, xsection_p25=bl.p10,
            pct_below=round(pct_below, 1), abs_saving=saving,
            deal_class="standard", gate_mode="baseline",
            baseline_median=bl.median, baseline_p10=bl.p10,
            score=score_candidate(pct_below, saving, route_class, config),
            found_at=o.found_at,
            rejected_reason=_guardrail_kill(conn, o, config, now),
        ))

    by_class: dict[str, list[Observation]] = {}
    for o in immature:
        by_class.setdefault(classify_route(o.dest, config), []).append(o)

    for route_class, routes in by_class.items():
        if len(routes) < MIN_CLASS_SIZE:
            continue  # no meaningful cross-section yet
        prices = sorted(r.price for r in routes)
        median = int(statistics.median(prices))
        p25 = int(statistics.quantiles(prices, n=4, method="inclusive")[0])
        floor = config.floors[route_class]
        for o in routes:
            saving = median - o.price
            if median <= 0 or saving < floor:
                continue
            pct_below = 100.0 * saving / median
            if pct_below < config.crosssection_pct:
                continue
            cand = Candidate(
                origin=o.origin, dest=o.dest,
                depart_date=o.depart_date, return_date=o.return_date,
                price=o.price, currency=o.currency,
                route_class=route_class,
                xsection_median=median, xsection_p25=p25,
                pct_below=round(pct_below, 1), abs_saving=saving,
                deal_class="standard",  # reclassified after verification
                score=score_candidate(pct_below, saving, route_class, config),
                found_at=o.found_at,
                rejected_reason=_guardrail_kill(conn, o, config, now),
            )
            out.append(cand)

    out.sort(key=lambda c: c.score, reverse=True)

    # Daily candidate cap counts deals already created today plus the
    # survivors we are about to admit this run.
    already = deals_created_today(conn, today_prefix=today_prefix)
    budget = max(0, config.daily_candidate_cap - already)
    survivors = 0
    capped: list[Candidate] = []
    for c in out:
        if c.rejected_reason is None:
            if survivors >= min(budget, config.max_candidates_per_run):
                c = _kill(c, "daily_cap" if survivors >= budget else "run_cap")
            else:
                survivors += 1
        capped.append(c)
    return capped


def _kill(c: Candidate, reason: str) -> Candidate:
    from dataclasses import replace
    return replace(c, rejected_reason=reason)


def _guardrail_kill(conn, o: Observation, config: DealConfig,
                    now: datetime) -> str | None:
    """Cooldown + dedup vs the route's last alerted deal. BOTH are
    bounded to the cooldown window: they exist to stop near-in-time
    repeats of the same fare event (D2/D3), not to mute a route forever
    around a price it once alerted at — a seasonal deal recurring months
    later at a similar price must alert again."""
    last = last_deal_for_route(conn, origin=o.origin, dest=o.dest)
    if last is None:
        return None
    last_price = last["price"]
    last_at = str(last["created_at"] or "")
    try:
        last_dt = datetime.strptime(last_at[:10], "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None
    if now - last_dt >= timedelta(days=config.route_cooldown_days):
        return None  # outside the window: neither dedup nor cooldown
    # Dedup: same route within +/- band% of the last deal price.
    band = config.dedup_band_pct / 100.0
    if last_price and abs(o.price - last_price) <= last_price * band:
        return "dedup_price_band"
    # Cooldown: unless a further -X% below the last price re-opens it.
    breaks = (last_price
              and o.price <= last_price * (1 - config.cooldown_break_pct / 100.0))
    if not breaks:
        return "route_cooldown"
    return None
