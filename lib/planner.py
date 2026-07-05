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
from datetime import date

from .config import RouteConfig
from .followup import select_candidates
from .sweep import SweepWindow, plan_windows

# Default per-run cap on Kiwi (300/mo tier; keep one run modest).
DEFAULT_KIWI_CAP = 20


@dataclass(frozen=True)
class Caps:
    """Per-source call caps for a single run. None = uncapped."""
    searchapi_sweep: int | None = None
    searchapi_followup: int | None = None
    skyscanner: int | None = None
    kiwi: int | None = DEFAULT_KIWI_CAP


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

    # ---- Followup candidates (SearchAPI point queries) ----
    followup_candidates: list[dict] = []
    if "searchapi" in src:
        cands = select_candidates(conn, route, today=today)
        if caps.searchapi_followup is not None and len(cands) > caps.searchapi_followup:
            notes.append(
                f"followup capped to {caps.searchapi_followup} of {len(cands)} "
                "candidates"
            )
            cands = cands[: caps.searchapi_followup]
        followup_candidates = cands

    # ---- Kiwi candidates (same selection, own cap) ----
    kiwi_candidates: list[dict] = []
    if "kiwi" in src:
        kc = select_candidates(conn, route, today=today)
        kiwi_cap = caps.kiwi if caps.kiwi is not None else DEFAULT_KIWI_CAP
        if len(kc) > kiwi_cap:
            notes.append(f"kiwi capped to {kiwi_cap} of {len(kc)} candidates")
            kc = kc[:kiwi_cap]
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
        "searchapi": len(sweep_windows) + len(followup_candidates),
        "aviasales": len(aviasales_pairs),
        "kiwi": len(kiwi_candidates),
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
