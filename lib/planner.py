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
    kiwi_band_days: int = 21,
    gf_cap: int = 25,
    serpapi_contingency: int = 7,
) -> dict[str, int]:
    """Closed-form, DB-free per-scan upper bounds for a search — what the
    creation form shows BEFORE any history exists. Pure geometry, no
    conn: mirrored 1:1 in web/src/lib/predict.ts, drift-guarded by a
    fixture this function generates in CI (scripts/gen_estimator_fixture).

    Kiwi discovery geometry matches build_run_plan exactly: one band per
    started `kiwi_band_days` chunk of the departure window, per
    (origin, destination) pair. Verification (googleflights) is quoted
    at its cap — it grows with findings and can never exceed the cap;
    the contingency line mirrors cost_vector's rule.
    """
    latest_dep = latest_return - timedelta(days=min_stay_days)
    window_days = max(0, (latest_dep - earliest_departure).days + 1)
    bands_per_pair = -(-window_days // kiwi_band_days) if window_days else 0
    pairs = n_origins * n_destinations
    return {
        "kiwi": bands_per_pair * pairs,
        "googleflights": gf_cap,
        "serpapi_contingency": min(gf_cap, serpapi_contingency),
        "aviasales": pairs,
    }


def cost_vector(plan: "RunPlan", *, caps: "Caps") -> CostVector:
    """Pure function of a RunPlan: the exact upper-bound cost lines the
    ledger reserves before executing it.

    Contingency: when googleflights is the followup rail and serpapi is
    an enabled source, run_scan._run_verification re-runs the candidate
    list through serpapi if the browser rail dies — that spend must be
    quoted and reserved too (an unreserved fallback either overspends
    the quote or silently drops verification; both forbidden).
    """
    lines: list[CostLine] = []
    notes = {
        "kiwi": "discovery bands + candidate checks",
        "googleflights": "verification (free politeness budget)",
        "serpapi": "verification",
        "searchapi": "verification (break-glass)",
        "aviasales": "cached sweep (unmetered, rate-paced)",
        "skyscanner": "curve + lookups",
    }
    for source, units in plan.calls_by_source.items():
        if units:
            lines.append(CostLine(source=source, units=units, kind="primary",
                                  note=notes.get(source, "")))
    if (plan.followup_source == "googleflights"
            and "serpapi" in plan.sources and plan.followup_candidates):
        fallback_units = min(len(plan.followup_candidates),
                             caps.serpapi or 0)
        if fallback_units:
            lines.append(CostLine(
                source="serpapi", units=fallback_units, kind="contingency",
                note="only if the browser rail dies mid-batch"))
    return CostVector(lines=tuple(lines))


@dataclass(frozen=True)
class Caps:
    """Per-source call caps for a single run. None = uncapped."""
    searchapi_sweep: int | None = None
    searchapi_followup: int | None = None
    skyscanner: int | None = None
    kiwi: int | None = DEFAULT_KIWI_CAP
    googleflights: int | None = 30   # free but polite — page renders
    # 100 renewing searches/month; 13 scheduled runs x 7 ~= 91 keeps a
    # margin for manual dispatches.
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


def build_run_plan(
    conn,
    route: RouteConfig,
    *,
    sources: list[str],
    caps: Caps,
    today: date | None = None,
) -> RunPlan:
    """Compute the exact plan for a run. No API calls, DB reads only."""
    today = today or date.today()
    src = tuple(sources)
    notes: list[str] = []

    # ---- Sweep (SearchAPI grid) ----
    sweep_windows: list[SweepWindow] = []
    if "searchapi" in src:
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

    # ---- Followup verification: free ladder ----
    # First enabled source wins: googleflights (free, needs a local
    # browser) > serpapi (managed, 100/mo renewing) > searchapi
    # (one-time credits, break-glass).
    followup_source = "searchapi"
    followup_cap = caps.searchapi_followup
    for cand_source, cand_cap in (
        ("googleflights", caps.googleflights),
        ("serpapi", caps.serpapi),
        ("searchapi", caps.searchapi_followup),
    ):
        if cand_source in src:
            followup_source = cand_source
            followup_cap = cand_cap
            break
    followup_candidates: list[dict] = []
    if followup_source in src:
        cands = select_candidates(conn, route, today=today)
        if followup_cap is not None and len(cands) > followup_cap:
            notes.append(
                f"followup ({followup_source}) capped to {followup_cap} of "
                f"{len(cands)} candidates"
            )
            cands = cands[:followup_cap]
        followup_candidates = cands

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
    kiwi_candidates: list[dict] = []
    if "kiwi" in src and "googleflights" not in src:
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

    # ---- Aviasales pairs (1 cheap_prices call per origin-destination) ----
    aviasales_pairs: list[tuple[str, str]] = []
    if "aviasales" in src:
        aviasales_pairs = [
            (o, d) for o in route.origins for d in route.destinations
        ]

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
        "serpapi": (
            len(followup_candidates) if followup_source == "serpapi" else 0
        ),
        "aviasales": len(aviasales_pairs),
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
