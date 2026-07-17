"""Single source of truth for "what will a run do".

The UI quote and the actual execution used to plan independently, so
they drifted (the quote showed 179 followup calls when execution ran 20;
it ignored smart-skip and omitted Aviasales/Kiwi entirely). This module
builds ONE `RunPlan` object: the exact sweep windows (post smart-skip,
post horizon/past clamp, post cap), the exact followup + Kiwi candidate
lists (window-filtered, month-diversified, capped), the Aviasales /
Sky Scrapper pairs, per-source call totals, and human-readable notes.

The quote renders this RunPlan. `run_all` executes the SAME RunPlan.
Preview == execution by construction.

All DB access is via `conn.execute(...).fetchone()/fetchall()` with
Row-key access, which works identically on stdlib sqlite3 and on the
`lib.turso_http.TursoConnection` used in the cloud deploy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import RouteConfig
from .followup import select_candidates
from .sweep import SweepWindow, plan_windows

# Default per-run cap on Kiwi (300/mo tier; keep one run modest).
DEFAULT_KIWI_CAP = 20

# SerpApi discovery grid: the RELIABLE finding layer after Kiwi's
# retirement and Google-Flights scraping getting captcha-walled from CI
# (2026-07-14). SerpApi is managed Google-Flights data that never gets
# blocked, so it prices a small rotating date grid across the window each
# scan. GRID + OTA must stay within the serpapi pool's per_search_cap (7,
# lib/sources.py) so non-owner searches reserve cleanly, and inside the
# free 250/mo: (GRID 5 + OTA 2) x 2 searches x 13 runs/mo = 182 < 250.
# A third search trips the create-form capacity gate → $25/mo switch.
SERPAPI_DISCOVERY_CAP = 5      # distinct date samples priced per scan
SERPAPI_OTA_RESERVE = 2        # enrich_ota_sellers: point_query + booking_options


@dataclass(frozen=True)
class CostLine:
    """One quoted budget line. kind='contingency' lines are reserved but
    only spent when the primary rail fails — shown separately everywhere
    (preview, digest, ledger)."""
    source: str
    units: int
    kind: str          # 'primary' | 'contingency'
    note: str


@dataclass(frozen=True)
class CostVector:
    """Worst-case units per source for one RunPlan — what the ledger
    reserves and the user sees. PREDICTED = GUARANTEED UPPER BOUND."""
    lines: tuple[CostLine, ...]

    def total(self, source: str, *, kind: str | None = None) -> int:
        return sum(l.units for l in self.lines
                   if l.source == source and (kind is None or l.kind == kind))

    def by_source(self, *, kind: str | None = None) -> dict[str, int]:
        out: dict[str, int] = {}
        for l in self.lines:
            if kind is None or l.kind == kind:
                out[l.source] = out.get(l.source, 0) + l.units
        return out


def predict_upper_bounds(
    *,
    n_origins: int,
    n_destinations: int,
    earliest_departure: date,
    latest_return: date,
    min_stay_days: int,
    trip_type: str = "round_trip",
    kiwi_band_days: int = 21,
    gf_cap: int = 25,
    serpapi_discovery_cap: int = SERPAPI_DISCOVERY_CAP,
    serpapi_ota_reserve: int = SERPAPI_OTA_RESERVE,
) -> dict[str, int]:
    """Closed-form, DB-free per-scan upper bounds for a search — what the
    creation form shows BEFORE any history exists. Pure geometry, no
    conn: mirrored 1:1 in web/src/lib/predict.ts, drift-guarded by a
    fixture this function generates in CI (scripts/gen_estimator_fixture).

    `serpapi` is the metered rail that gates capacity: a fixed-size live
    discovery grid (capped, so window size only ever lowers the actual
    count) plus the OTA seller-check reserve. `googleflights` verification
    and `aviasales` discovery are free. `kiwi` is retired but its band
    geometry stays quoted (unused by the roster) for continuity.
    Aviasales corroboration: 1 call per pair (round-trip) or 1 per pair
    per window month (one-way).
    """
    one_way = trip_type == "one_way"
    # One-way departures span the whole window (no return leg to fit).
    latest_dep = latest_return if one_way else (
        latest_return - timedelta(days=min_stay_days))
    window_days = max(0, (latest_dep - earliest_departure).days + 1)
    bands_per_pair = -(-window_days // kiwi_band_days) if window_days else 0
    pairs = n_origins * n_destinations
    serpapi = serpapi_discovery_cap + serpapi_ota_reserve
    if one_way:
        months = 0 if not window_days else (
            (latest_dep.year - earliest_departure.year) * 12
            + latest_dep.month - earliest_departure.month + 1)
        return {
            "kiwi": bands_per_pair * pairs,
            "googleflights": gf_cap,
            "serpapi": serpapi,
            "aviasales": months * pairs,
        }
    return {
        "kiwi": bands_per_pair * pairs,
        "googleflights": gf_cap,
        "serpapi": serpapi,
        "aviasales": pairs,
    }


def cost_vector(plan: "RunPlan", *, caps: "Caps") -> CostVector:
    """Pure function of a RunPlan: the exact upper-bound cost lines the
    ledger reserves before executing it.

    SerpApi is the metered PRIMARY discovery rail now (2026-07-14): its
    line covers the live date grid plus the OTA seller check, both inside
    calls_by_source['serpapi']. It is no longer a browser-death
    contingency — the grid IS the reliable live layer, so a dead gf
    scraper degrades to serpapi's own coverage (surfaced by health, not
    silently), and there is no separate serpapi fallback to reserve.
    """
    lines: list[CostLine] = []
    notes = {
        "kiwi": "discovery bands + candidate checks",
        "googleflights": "verification (free politeness budget)",
        "serpapi": "live discovery grid + OTA seller check",
        "searchapi": "verification (break-glass)",
        "aviasales": "cached sweep (unmetered, rate-paced)",
        "skyscanner": "curve + lookups",
    }
    for source, units in plan.calls_by_source.items():
        if units:
            lines.append(CostLine(source=source, units=units, kind="primary",
                                  note=notes.get(source, "")))
    return CostVector(lines=tuple(lines))


@dataclass(frozen=True)
class Caps:
    """Per-source call caps for a single run. None = uncapped."""
    searchapi_sweep: int | None = None
    searchapi_followup: int | None = None
    skyscanner: int | None = None
    kiwi: int | None = DEFAULT_KIWI_CAP
    googleflights: int | None = 30   # free but polite — page renders
    # 250 renewing searches/month (re-verified from /account 2026-07-07);
    # 13 scheduled runs x 7 ~= 91 leaves ample margin for manual
    # dispatches and multi-search batches.
    serpapi: int | None = 7


@dataclass(frozen=True)
class KiwiBand:
    """One Kiwi range-search call: a departure band with the inbound
    band derived from the stay range."""
    origin: str
    destination: str
    outbound_start: date
    outbound_end: date
    inbound_start: date
    inbound_end: date


@dataclass(frozen=True)
class RunPlan:
    route: RouteConfig
    today: date
    sources: tuple[str, ...]
    sweep_windows: tuple[SweepWindow, ...]
    followup_candidates: tuple[dict, ...]
    kiwi_candidates: tuple[dict, ...]
    aviasales_pairs: tuple[tuple[str, str], ...]
    skyscanner_pairs: tuple[tuple[str, str], ...]
    calls_by_source: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    # Which source verifies the followup candidates. Free ladder:
    # googleflights when enabled, else searchapi.
    followup_source: str = "searchapi"
    # Kiwi discovery: one range-search call per band (max ~3-week bands).
    kiwi_bands: tuple[KiwiBand, ...] = ()
    # One-way only: the "YYYY-MM" months the aviasales corroboration
    # sweeps (one call per pair per month). Quote == execution: the
    # runner iterates exactly this list.
    aviasales_months: tuple[str, ...] = ()
    # SerpApi discovery grid: distinct (origin, dest, dep, ret) date
    # samples SerpApi prices live each scan (the reliable finding layer;
    # runner feeds these straight into run_followup with the serpapi
    # client). Same shape as followup_candidates.
    serpapi_discovery: tuple[dict, ...] = ()


# Golden-ratio conjugate: phase steps by frac(ordinal * PHI) are
# quasi-uniform and never resonate with ANY integer period — the naive
# `ordinal % seg` phase resonated with the Mon/Wed/Sat cadence (audit
# 2026-07-16: 3 of 13 one-way scans re-priced already-covered dates,
# coverage plateaued at 51.5% instead of ~65%).
_PHI = 0.6180339887498949


def _discovery_grid(route: RouteConfig, *, today: date,
                    max_points: int) -> list[dict]:
    """Up to `max_points` DISTINCT departure dates sampled evenly across
    the search window, so SerpApi can price the whole window live — the
    discovery mechanism now that Kiwi is retired and gf scraping is
    blocked. Origins/destinations are assigned round-robin across the
    dates (date breadth beats per-date origin completeness for a scout —
    the mission flies from MAD *or* BCN, either is fine).

    TWO deterministic rotations, both derived from `today` (no clock
    read, so quote == execution holds):
      * the sampling PHASE (golden-ratio, see _PHI) walks the departure
        axis scan over scan without resonating with the scan cadence;
      * for ROUND-TRIP routes the STAY length rotates per point and per
        scan across [min_stay, max_stay] (clamped so the return fits the
        window), so the grid samples the whole (departure x stay)
        rectangle instead of the min-stay edge — the stay axis was 97%
        blind before this (audit 2026-07-16). One-way uses the ''
        sentinel. Shape matches select_candidates so run_followup
        consumes it unchanged."""
    from datetime import timedelta as _td
    sw = route.search_window
    one_way = route.is_one_way
    min_stay = route.stay.min_days
    max_stay = route.stay.max_days
    earliest = max(sw.earliest_departure, today)
    latest_dep = sw.latest_return if one_way else (
        sw.latest_return - _td(days=min_stay))
    pairs = [(o, d) for o in route.origins for d in route.destinations]
    if not pairs or max_points <= 0 or latest_dep < earliest:
        return []
    span = (latest_dep - earliest).days
    n = min(max_points, span + 1)            # no more dates than days exist
    if n <= 1:
        offsets = [span // 2]
    else:
        seg = span / n
        phase = ((today.toordinal() * _PHI) % 1.0) * seg
        offsets = [min(span, round(i * seg + phase)) for i in range(n)]
    # Evenly-spaced stay samples across the range; which point gets which
    # stay shifts with `today`, so over scans every departure region gets
    # priced at every sampled stay length.
    stay_steps = [min_stay + round(k * (max_stay - min_stay) / max(1, n - 1))
                  for k in range(n)]
    out: list[dict] = []
    seen: set[int] = set()
    for i, off in enumerate(offsets):
        if off in seen:
            continue
        seen.add(off)
        o, d = pairs[i % len(pairs)]
        dep = earliest + _td(days=off)
        if one_way:
            ret = ""
        else:
            stay = stay_steps[(i + today.toordinal()) % n]
            # Late departures can't host the longer stays — clamp to the
            # window end (never below min_stay: dep <= latest_return-min).
            ret_d = min(dep + _td(days=stay), sw.latest_return)
            ret = ret_d.isoformat()
        out.append({"origin": o, "destination": d,
                    "departure_date": dep.isoformat(),
                    "return_date": ret, "snapshot_price": None,
                    "trigger": "grid"})
    return out


def build_run_plan(
    conn,
    route: RouteConfig,
    *,
    sources: list[str],
    caps: Caps,
    today: date | None = None,
    pool_states: dict | None = None,
) -> RunPlan:
    """Compute the exact plan for a run. No API calls, DB reads only.

    `pool_states`: optional {source: PoolState} (from
    ledger.all_pool_states()). When given, a monthly source whose pool is
    floored (a *_floor anchor, e.g. a 402 payment wall) or exhausted
    (effective_available <= 0) is DROPPED before any cost line is built —
    so one dead pool degrades the search to its healthy sources instead
    of the all-or-nothing reservation skipping the WHOLE search (the
    2026-07-11 failure: a floored Kiwi silently took down every search,
    owner included). Pure narrowing: it can only REDUCE the plan below
    the all-healthy upper bound, so the estimator/quote is untouched."""
    today = today or date.today()
    notes: list[str] = []
    src_list = list(sources)
    if pool_states:
        kept = []
        for s in src_list:
            st = pool_states.get(s)
            floored = st is not None and (st.baseline_origin or "").endswith("_floor")
            exhausted = (st is not None and st.effective_available is not None
                         and st.effective_available <= 0)
            if floored or exhausted:
                why = ("payment/exhaustion floor" if floored
                       else "pool exhausted")
                notes.append(f"{s} dropped: {why} "
                             f"({st.baseline_origin or 'n/a'})")
                continue
            kept.append(s)
        src_list = kept
    src = tuple(src_list)

    # ---- Sweep (SearchAPI grid) ----
    # Round-trip only: the calendar engine prices (dep x ret) rectangles;
    # a one-way route has no return axis (min_stay=0 would degenerate the
    # geometry). One-way discovery rides the serpapi grid + gf instead.
    sweep_windows: list[SweepWindow] = []
    if "searchapi" in src and not route.is_one_way:
        planned, geo_notes = plan_windows(route, today=today)
        notes.extend(geo_notes)
        # Apply smart-skip exactly as run_sweep would — but batched:
        # one DB query for all windows instead of one per window. On the
        # Turso HTTP backend each query is a network round trip, and the
        # quote recomputes on every UI interaction; 86 sequential round
        # trips would make the page unusable.
        skip_flags = _batched_skip_decisions(conn, route, planned, today)
        kept: list[SweepWindow] = []
        skipped = 0
        for w, skip in zip(planned, skip_flags):
            if skip:
                skipped += 1
            else:
                kept.append(w)
        if skipped:
            notes.append(
                f"{skipped} sweep windows skipped (prior min above threshold)"
            )
        # Apply the sweep cap.
        if caps.searchapi_sweep is not None and len(kept) > caps.searchapi_sweep:
            notes.append(
                f"sweep capped to {caps.searchapi_sweep} of {len(kept)} windows"
            )
            kept = kept[: caps.searchapi_sweep]
        sweep_windows = kept

    # ---- Followup verification ladder ----
    # First enabled source wins: googleflights (free browser scraper) >
    # searchapi (one-time credits, break-glass). SerpApi is DELIBERATELY
    # NOT on this ladder: it is the metered discovery grid (below), and
    # letting it ALSO verify candidates would double-count it — grid +
    # OTA + candidates = up to 14 serpapi/scan, blowing the fixed 7-unit
    # upper bound and the per_search_cap (found in adversarial review
    # 2026-07-14). When gf is dead, the serpapi grid IS the live layer;
    # aviasales leads simply go gf-verified-or-not, never via metered
    # serpapi. So serpapi's per-scan cost stays a flat grid + OTA.
    followup_source = "searchapi"
    followup_cap = caps.searchapi_followup
    for cand_source, cand_cap in (
        ("googleflights", caps.googleflights),
        ("searchapi", caps.searchapi_followup),
    ):
        if cand_source in src:
            followup_source = cand_source
            followup_cap = cand_cap
            break
    # Round-trip candidates are (dep, ret) pairs; one-way candidates
    # carry the '' return sentinel, which select_candidates and the
    # executors map to a one-way point query (return_=None). gf verifies
    # both trip types; the searchapi rung is round-trip only (break-glass
    # source, 2 credits left: not worth an unverified one-way param).
    followup_candidates: list[dict] = []
    if route.is_one_way and followup_source == "searchapi":
        if "searchapi" in src:
            notes.append("one-way verification needs googleflights or "
                         "serpapi (searchapi adapter is round-trip only)")
    elif followup_source in src:
        cands = select_candidates(conn, route, today=today)
        if followup_cap is not None and len(cands) > followup_cap:
            notes.append(
                f"followup ({followup_source}) capped to {followup_cap} of "
                f"{len(cands)} candidates"
            )
            cands = cands[:followup_cap]
        followup_candidates = cands

    # ---- SerpApi discovery grid (RELIABLE live discovery) ----
    # Kiwi is retired and Google-Flights scraping is blocked from CI
    # (2026-07-14 run: 0/25 grid points, every query captcha-walled) — so
    # the FINDING now runs on SerpApi, a managed Google-Flights API that
    # never gets captcha'd. It prices a small evenly-spaced date grid
    # across the window each scan; that IS live verification (SerpApi is
    # live Google data), so no separate gf pass is needed. Bounded by
    # SERPAPI_DISCOVERY_CAP so it stays inside the free 250/mo tier at the
    # owner's scale (grid + 1 booking_options ≈ 6 calls/search/scan).
    serpapi_discovery: list[dict] = []
    if "serpapi" in src:
        serpapi_discovery = _discovery_grid(
            route, today=today, max_points=SERPAPI_DISCOVERY_CAP)

    # ---- Kiwi discovery bands (range-search, 1 call per band) ----
    kiwi_bands: list[KiwiBand] = []
    if "kiwi" in src:
        from datetime import timedelta
        sw = route.search_window
        band_days = 21
        earliest = max(sw.earliest_departure, today)
        latest_dep = sw.latest_return - timedelta(days=route.stay.min_days)
        for origin in route.origins:
            for destination in route.destinations:
                start = earliest
                while start <= latest_dep:
                    end = min(start + timedelta(days=band_days - 1), latest_dep)
                    kiwi_bands.append(KiwiBand(
                        origin=origin, destination=destination,
                        outbound_start=start, outbound_end=end,
                        inbound_start=start + timedelta(days=route.stay.min_days),
                        inbound_end=min(
                            end + timedelta(days=route.stay.max_days),
                            sw.latest_return,
                        ),
                    ))
                    start = end + timedelta(days=1)
        if kiwi_bands:
            notes.append(
                f"kiwi discovery: {len(kiwi_bands)} range bands "
                f"(~{band_days}d each, cheapest ~50 itineraries per band)"
            )

    # ---- Kiwi point candidates (only when kiwi is the followup fallback,
    # i.e. selected WITHOUT googleflights) ----
    # One-way stays gated off DELIBERATELY: run_kiwi_followup is a
    # round-trip search (it would crash on the '' sentinel), and kiwi
    # point checks would double-spend the scarce 300/mo discovery pool —
    # one-way verification rides the free rails only.
    kiwi_candidates: list[dict] = []
    if "kiwi" in src and "googleflights" not in src and not route.is_one_way:
        kc = select_candidates(conn, route, today=today)
        kiwi_cap = caps.kiwi if caps.kiwi is not None else DEFAULT_KIWI_CAP
        # Bands consume budget too; leave room.
        kiwi_point_cap = max(0, kiwi_cap - len(kiwi_bands))
        if len(kc) > kiwi_point_cap:
            notes.append(
                f"kiwi point-followup capped to {kiwi_point_cap} of {len(kc)}"
            )
            kc = kc[:kiwi_point_cap]
        kiwi_candidates = kc

    # ---- Aviasales cached corroboration ----
    # Round-trip: 1 cheap_prices call per (origin, destination).
    # One-way: 1 one_way_month_prices call per (pair, window month) —
    # /aviasales/v3/prices_for_dates one_way=true returns the cheapest
    # cached ticket per departure day of that month (probed 2026-07-08).
    aviasales_pairs: list[tuple[str, str]] = []
    aviasales_months: list[str] = []
    if "aviasales" in src:
        aviasales_pairs = [
            (o, d) for o in route.origins for d in route.destinations
        ]
        if route.is_one_way:
            sw = route.search_window
            m = max(sw.earliest_departure, today)
            m = date(m.year, m.month, 1)
            while m <= sw.latest_return:
                aviasales_months.append(m.strftime("%Y-%m"))
                m = (date(m.year + 1, 1, 1) if m.month == 12
                     else date(m.year, m.month + 1, 1))

    # ---- Sky Scrapper pairs (1 curve call/pair + airport lookups) ----
    skyscanner_pairs: list[tuple[str, str]] = []
    skyscanner_lookups = 0
    if "skyscanner" in src:
        seen_codes: set[str] = set()
        for o in route.origins:
            for d in route.destinations:
                skyscanner_pairs.append((o, d))
                for code in (o, d):
                    if code in seen_codes:
                        continue
                    seen_codes.add(code)
                    # An uncached IATA costs 1 searchAirport call.
                    from . import db as db_mod
                    if db_mod.lookup_airport(conn, code) is None:
                        skyscanner_lookups += 1

    # ---- Per-source call totals ----
    # Sky Scrapper point queries during followup cost ~2 calls each
    # (kickoff + 1 poll). We don't drive those from a separate list here;
    # they're bounded by caps.skyscanner and surfaced only if skyscanner
    # is an active followup source. For the quote we count curve + lookups.
    calls = {
        "searchapi": len(sweep_windows) + (
            len(followup_candidates) if followup_source == "searchapi" else 0
        ),
        "googleflights": (
            len(followup_candidates) if followup_source == "googleflights" else 0
        ),
        # SerpApi = live discovery grid + OTA seller-check reserve. It is
        # NOT a followup verifier (see ladder above), so this stays a
        # fixed grid + OTA — a flat, honest upper bound the capacity gate
        # and per_search_cap can both rely on.
        "serpapi": (
            len(serpapi_discovery)
            + (SERPAPI_OTA_RESERVE if "serpapi" in src else 0)
        ),
        "aviasales": len(aviasales_pairs) * (
            len(aviasales_months) if route.is_one_way else 1
        ),
        "kiwi": len(kiwi_bands) + len(kiwi_candidates),
        "skyscanner": len(skyscanner_pairs) + skyscanner_lookups,
    }
    if skyscanner_lookups:
        notes.append(
            f"{skyscanner_lookups} one-time Sky Scrapper airport lookups "
            "(cached after first run)"
        )

    return RunPlan(
        route=route,
        today=today,
        sources=src,
        sweep_windows=tuple(sweep_windows),
        followup_candidates=tuple(followup_candidates),
        kiwi_candidates=tuple(kiwi_candidates),
        aviasales_pairs=tuple(aviasales_pairs),
        skyscanner_pairs=tuple(skyscanner_pairs),
        calls_by_source=calls,
        notes=tuple(notes),
        followup_source=followup_source,
        kiwi_bands=tuple(kiwi_bands),
        aviasales_months=tuple(aviasales_months),
        serpapi_discovery=tuple(serpapi_discovery),
    )


def _batched_skip_decisions(
    conn, route: RouteConfig, windows: list[SweepWindow], today: date,
) -> list[bool]:
    """Smart-skip decisions for all windows via ONE DB query.

    Mirrors lib.sweep._should_skip_window exactly:
      skip iff skip_if_min_above and skip_grace_days are configured,
      the window's outbound_start is more than grace days out,
      the window box has at least one prior snapshot,
      and the most recent snapshot's min price exceeds the threshold.

    The per-window version runs 1 query per window — fine for the CLI,
    unusable through the Turso HTTP backend at quote time (86 round
    trips per rerun). This pulls the relevant rows once and evaluates
    in Python.
    """
    threshold = route.sweep.skip_if_min_above
    grace = route.sweep.skip_grace_days
    if threshold is None or grace is None or not windows:
        return [False] * len(windows)

    rows = conn.execute(
        """
        SELECT origin, destination, departure_date, return_date,
               price, snapshot_at
        FROM calendar_snapshots
        WHERE route_id = ? AND source = 'searchapi'
        """,
        (route.name,),
    ).fetchall()

    decisions: list[bool] = []
    for w in windows:
        if (w.outbound_start - today).days <= grace:
            decisions.append(False)
            continue
        ob_lo, ob_hi = w.outbound_start.isoformat(), w.outbound_end.isoformat()
        rt_lo, rt_hi = w.return_start.isoformat(), w.return_end.isoformat()
        in_box = [
            r for r in rows
            if r["origin"] == w.origin and r["destination"] == w.destination
            and ob_lo <= r["departure_date"] <= ob_hi
            and rt_lo <= r["return_date"] <= rt_hi
        ]
        if not in_box:
            decisions.append(False)  # never scanned -> always scan
            continue
        latest_snap = max(r["snapshot_at"] for r in in_box)
        min_price = min(r["price"] for r in in_box
                        if r["snapshot_at"] == latest_snap)
        decisions.append(min_price > threshold)
    return decisions
