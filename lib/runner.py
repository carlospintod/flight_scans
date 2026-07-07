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
from .scanops import run_aviasales_sweep, run_kiwi_discovery

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
    the primary verification rail dies. The batch runner passes the
    search's RESERVED contingency units (A7: fallback must stay inside
    this search's reservation, never borrow other searches' holds);
    run_scan.py passes caps.serpapi (legacy single-search behavior).
    """
    out = SearchRunResult(search_id=route.name)
    if serpapi_fallback_cap is None:
        serpapi_fallback_cap = caps.serpapi or 0

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
        try:
            stored = run_aviasales_sweep(
                conn, clients["aviasales"], route, dry_run=False,
                pairs=list(plan.aviasales_pairs))
            out.record("aviasales", attempted=len(plan.aviasales_pairs),
                       stored=stored)
        except Exception as exc:  # noqa: BLE001
            out.degraded = True
            out.record("aviasales", attempted=len(plan.aviasales_pairs),
                       error=str(exc))
            LOG.error("aviasales failed (%s): %s", route.name, exc)

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
