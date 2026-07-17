"""Per-search execution core, shared by run_scan.py (single) and
run_batch.py (multi-search batch runner).

One search's slice of a run: kiwi discovery bands -> verification via
the followup ladder (with the serpapi contingency fallback) -> aviasales
cached sweep -> alert evaluation. Pure orchestration — every step
delegates to the existing scanops/followup/alerts machinery, and the
clients arrive already wrapped in GuardedClient so quota enforcement is
invisible here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import alerts as alerts_mod
from .followup import run_followup
from .scanops import enrich_ota_sellers, run_aviasales_sweep, run_kiwi_discovery

LOG = logging.getLogger(__name__)


@dataclass
class SearchRunResult:
    search_id: str
    degraded: bool = False
    results: dict = field(default_factory=dict)   # source -> {attempted, stored, error}
    verification_source: str | None = None
    alerts: list = field(default_factory=list)    # AlertRow list

    def record(self, source: str, *, attempted: int = 0, stored: int = 0,
               error: str | None = None) -> None:
        self.results[source] = {"attempted": attempted, "stored": stored,
                                "error": error}

    @property
    def rows_stored(self) -> int:
        return sum(r["stored"] for r in self.results.values())


def execute_search(*, conn, route, plan, clients, caps,
                   alerts_log: Path,
                   serpapi_fallback_cap: int | None = None) -> SearchRunResult:
    """Execute one search's RunPlan slices with the given clients.

    `serpapi_fallback_cap`: max candidates re-run through serpapi when
    the free gf rail dies. DEFAULTS TO 0 (2026-07-14): serpapi now spends
    its whole reserved budget on the discovery grid + OTA, so an extra
    gf->serpapi verification fallback would spend UNRESERVED serpapi and
    break the upper bound. The grid is the reliable live layer instead.
    run_batch passes cost.total('serpapi', kind='contingency') == 0.
    """
    out = SearchRunResult(search_id=route.name)
    if serpapi_fallback_cap is None:
        serpapi_fallback_cap = 0
    from .scanops import _now_iso
    search_started = _now_iso()

    # --- Kiwi discovery bands (fails gracefully while quota is 0) ---
    if plan.kiwi_bands and clients.get("kiwi") is not None:
        try:
            stored = run_kiwi_discovery(
                conn, clients["kiwi"], route,
                bands=list(plan.kiwi_bands), dry_run=False)
            out.record("kiwi", attempted=len(plan.kiwi_bands), stored=stored)
        except Exception as exc:  # noqa: BLE001
            out.degraded = True
            out.record("kiwi", attempted=len(plan.kiwi_bands), error=str(exc))
            LOG.error("kiwi discovery failed (%s): %s", route.name, exc)

    # --- SearchAPI rectangle sweep (full-coverage discovery) ---
    # google_flights_calendar prices whole (dep x ret) rectangles — the
    # only layer that sees EVERY date combination (2026-07-16 coverage
    # audit: the point-query grid samples ~4% of the rectangle; this
    # sweeps 100%). Finite lifetime credits: run_batch gates it to
    # biweekly owner runs, the ledger meters every call, and run_sweep
    # executes EXACTLY the planned windows (quote == execution).
    if plan.sweep_windows and clients.get("searchapi") is not None:
        from .sweep import run_sweep
        try:
            sres = run_sweep(conn=conn, client=clients["searchapi"],
                             route=route, windows=list(plan.sweep_windows),
                             dry_run=False)
            out.record("searchapi", attempted=len(plan.sweep_windows),
                       stored=sres.entries_stored)
        except Exception as exc:  # noqa: BLE001 — incl. QuotaExceeded
            out.degraded = True
            out.record("searchapi", attempted=len(plan.sweep_windows),
                       error=str(exc))
            LOG.error("searchapi sweep failed (%s): %s", route.name, exc)

    # --- SerpApi live discovery grid (the reliable finding layer) ---
    # Kiwi is retired and gf scraping is captcha-walled from CI
    # (2026-07-14), so SerpApi — managed Google Flights that never gets
    # blocked — prices a rotating date grid across the window each scan.
    # run_followup stores the same calendar+point rows as any point query
    # (source='serpapi'); bounded by the plan's grid and the search's
    # reserved serpapi budget, with the OTA check drawing the remainder.
    if plan.serpapi_discovery and clients.get("serpapi") is not None:
        grid = list(plan.serpapi_discovery)
        try:
            res = run_followup(conn=conn, client=clients["serpapi"],
                               route=route, candidates=grid,
                               skyscanner_max_calls=0)
            out.record("serpapi", attempted=len(grid),
                       stored=res.rows_stored)
        except Exception as exc:  # noqa: BLE001 — incl. QuotaExceeded
            out.degraded = True
            out.record("serpapi", attempted=len(grid), error=str(exc))
            LOG.error("serpapi discovery grid failed (%s): %s",
                      route.name, exc)

    # --- Verification via the followup ladder ---
    candidates = list(plan.followup_candidates)
    if candidates:
        v_stored, v_queried, v_source, v_error = _run_verification(
            conn=conn, route=route, candidates=candidates,
            plan_source=plan.followup_source, clients=clients,
            fallback_cap=serpapi_fallback_cap)
        out.record(v_source, attempted=len(candidates), stored=v_stored,
                   error=v_error)
        out.verification_source = v_source
        if v_error is not None:
            out.degraded = True

    # --- Aviasales cached sweep ---
    if plan.aviasales_pairs and clients.get("aviasales") is not None:
        n_av = len(plan.aviasales_pairs) * (
            len(plan.aviasales_months) if route.is_one_way else 1)
        try:
            stored = run_aviasales_sweep(
                conn, clients["aviasales"], route, dry_run=False,
                pairs=list(plan.aviasales_pairs),
                months=list(plan.aviasales_months))
            out.record("aviasales", attempted=n_av, stored=stored)
        except Exception as exc:  # noqa: BLE001
            out.degraded = True
            out.record("aviasales", attempted=n_av, error=str(exc))
            LOG.error("aviasales failed (%s): %s", route.name, exc)

    # --- OTA-seller enrichment (reliable OTA coverage) ---
    # For the cheapest verified fare, ask SerpApi booking_options which
    # OTA sells it cheaper than Google's headline. Rides the search's
    # already-reserved serpapi budget (so spend never exceeds the quote);
    # best-effort, before alerts so a cheaper OTA fare can fire one.
    if clients.get("serpapi") is not None:
        try:
            n = enrich_ota_sellers(conn, clients["serpapi"], route,
                                   since=search_started)
            if n:
                out.record("serpapi_ota", attempted=1, stored=n)
        except Exception as exc:  # noqa: BLE001
            LOG.info("ota enrichment skipped (%s): %s", route.name, exc)

    # --- Alerts (drop + new_low) ---
    out.alerts = alerts_mod.evaluate(conn=conn, route=route,
                                     log_path=alerts_log)
    return out


def _run_verification(*, conn, route, candidates, plan_source, clients,
                      fallback_cap: int):
    """Verify candidates via the planned source, falling back to serpapi.

    The fallback is the CI path: the plan may say googleflights, but on a
    runner without a browser the client is None (construction warning) or
    dies on the first query (GoogleFlightsUnavailable aborts the batch,
    itineraries_queried == 0). Either way the SAME candidate list runs
    through serpapi, capped at `fallback_cap` — a Google block must
    never silently drop verification, and under the ledger the fallback
    stays inside this search's reserved contingency units.

    Returns (rows_stored, itineraries_queried, source_used, error|None).
    """
    primary = clients.get(plan_source)
    if primary is not None:
        try:
            if hasattr(primary, "__exit__"):   # browser client: close after
                with primary as client:
                    res = run_followup(conn=conn, client=client, route=route,
                                       candidates=candidates,
                                       skyscanner_max_calls=0)
            else:
                res = run_followup(conn=conn, client=primary, route=route,
                                   candidates=candidates,
                                   skyscanner_max_calls=0)
            if res.itineraries_queried > 0:
                return res.rows_stored, res.itineraries_queried, plan_source, None
            LOG.warning("%s verified nothing (browser dead or all queries "
                        "failed) — trying serpapi fallback", plan_source)
        except Exception as exc:  # noqa: BLE001
            LOG.error("%s verification failed: %s — trying serpapi fallback",
                      plan_source, exc)
    else:
        LOG.info("%s unavailable — trying serpapi fallback", plan_source)

    fallback = clients.get("serpapi")
    if plan_source == "serpapi" or fallback is None or fallback_cap <= 0:
        return 0, 0, plan_source, (f"{plan_source} unavailable and no "
                                   f"serpapi fallback budget")
    capped = candidates[:fallback_cap]
    if len(capped) < len(candidates):
        LOG.info("serpapi fallback capped to %d of %d candidates",
                 len(capped), len(candidates))
    try:
        res = run_followup(conn=conn, client=fallback, route=route,
                           candidates=capped, skyscanner_max_calls=0)
        return res.rows_stored, res.itineraries_queried, "serpapi", None
    except Exception as exc:  # noqa: BLE001
        return 0, 0, "serpapi", str(exc)
