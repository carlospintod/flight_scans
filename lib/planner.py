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
from .sweep import SweepWindow, _should_skip_window, plan_windows

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
        # Apply smart-skip exactly as run_sweep would.
        kept: list[SweepWindow] = []
        skipped = 0
        for w in planned:
            skip, _reason = _should_skip_window(conn, route, w, today)
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
