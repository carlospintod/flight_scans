"""Headless scan runner — the CLI equivalent of the UI's RUN button.

Usage:
    python run_scan.py [--sources googleflights,serpapi,aviasales,kiwi]
                       [--cap 25] [--trigger local] [--json-summary out.json]

Builds a RunPlan for the route's effective config (DB wins, YAML seeds)
and executes it: Kiwi discovery bands (when quota allows), point
verification via the followup ladder (googleflights > serpapi >
searchapi) with an automatic serpapi fallback when the local browser is
unavailable — the CI path — then Aviasales bonus signal and alert
evaluation (drop + new_low). Respects TURSO_* env vars.

Exit codes: 0 = every requested source ran or was gracefully skipped;
1 = fatal (DB/config unusable); 2 = degraded (a source errored
unexpectedly). Non-zero turns the Actions run red.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
load_dotenv(dotenv_path=REPO / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOG = logging.getLogger("scan")

EXIT_OK, EXIT_FATAL, EXIT_DEGRADED = 0, 1, 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", default="spain-nairobi")
    ap.add_argument("--sources",
                    default="googleflights,serpapi,aviasales,kiwi")
    ap.add_argument("--cap", type=int, default=25,
                    help="max googleflights verifications this scan")
    ap.add_argument("--trigger", default="local",
                    choices=["local", "cron", "dispatch", "schedule",
                             "workflow_dispatch"],
                    help="what started this run (recorded in scan_runs)")
    ap.add_argument("--json-summary", type=Path, default=None,
                    help="write a machine-readable run summary here")
    args = ap.parse_args()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    # GitHub event names map onto our canonical trigger labels.
    trigger = {"schedule": "cron", "workflow_dispatch": "dispatch"}.get(
        args.trigger, args.trigger)

    from lib import alerts as alerts_mod
    from lib import db as db_mod
    from lib import route_store
    from lib.clients import make_clients
    from lib.followup import run_followup
    from lib.planner import Caps, build_run_plan
    from lib.scanops import run_aviasales_sweep, run_kiwi_discovery

    started_at = _now_iso()
    results: dict[str, dict] = {}   # source -> {attempted, stored, error}
    degraded = False

    def _result(source: str, *, attempted: int = 0, stored: int = 0,
                error: str | None = None) -> None:
        results[source] = {"attempted": attempted, "stored": stored,
                           "error": error}

    try:
        conn_cm = db_mod.connect(REPO / "data" / "tracker.db")
    except Exception as exc:  # noqa: BLE001
        LOG.error("FATAL: cannot connect to DB: %s", exc)
        return EXIT_FATAL

    with conn_cm as conn:
        try:
            db_mod.ensure_schema(conn)
            route, cfg_source = route_store.load_effective_route(
                conn, args.route, REPO / "routes")
        except Exception as exc:  # noqa: BLE001
            LOG.error("FATAL: schema/config unusable: %s", exc)
            return EXIT_FATAL
        LOG.info("route %s config source: %s", args.route, cfg_source)

        caps = Caps(googleflights=args.cap, kiwi=20,
                    searchapi_sweep=0, searchapi_followup=0)
        plan = build_run_plan(conn, route, sources=sources, caps=caps,
                              today=date.today())
        print("\n=== PLAN ===")
        for k, v in plan.calls_by_source.items():
            if v:
                print(f"  {k}: {v} calls")
        for n in plan.notes:
            print(f"  note: {n}")

        clients, client_warnings = make_clients(sources, conn)
        for w in client_warnings:
            LOG.warning("%s", w)   # graceful skip, not degradation

        # --- Kiwi discovery bands (fails gracefully while quota is 0) ---
        if plan.kiwi_bands and clients.get("kiwi") is not None:
            try:
                stored = run_kiwi_discovery(
                    conn, clients["kiwi"], route,
                    bands=list(plan.kiwi_bands), dry_run=False)
                _result("kiwi", attempted=len(plan.kiwi_bands), stored=stored)
                print(f"\nkiwi discovery: {stored} itineraries stored")
            except Exception as exc:  # noqa: BLE001
                degraded = True
                _result("kiwi", attempted=len(plan.kiwi_bands),
                        error=str(exc))
                LOG.error("kiwi discovery failed: %s", exc)

        # --- Verification via the followup ladder ---
        candidates = list(plan.followup_candidates)
        if candidates:
            v_stored, v_queried, v_source, v_error = _run_verification(
                conn=conn, route=route, candidates=candidates,
                plan_source=plan.followup_source, clients=clients,
                caps=caps, run_followup=run_followup)
            _result(v_source, attempted=len(candidates), stored=v_stored,
                    error=v_error)
            if v_error is not None:
                degraded = True
            print(f"\n{v_source} verification: {v_queried} itineraries, "
                  f"{v_stored} rows")

        # --- Aviasales bonus signal ---
        if plan.aviasales_pairs and clients.get("aviasales") is not None:
            try:
                stored = run_aviasales_sweep(
                    conn, clients["aviasales"], route, dry_run=False,
                    pairs=list(plan.aviasales_pairs))
                _result("aviasales", attempted=len(plan.aviasales_pairs),
                        stored=stored)
                print(f"aviasales: {stored} rows stored")
            except Exception as exc:  # noqa: BLE001
                degraded = True
                _result("aviasales", attempted=len(plan.aviasales_pairs),
                        error=str(exc))
                LOG.error("aviasales failed: %s", exc)

        # --- Alerts (drop + new_low) ---
        fired = alerts_mod.evaluate(
            conn=conn, route=route, log_path=REPO / "data" / "alerts.log")
        print(f"\nalerts fired: {len(fired)}")
        for a in fired:
            print(f"  [{a.alert_type}] {a.origin}->{a.destination} "
                  f"{a.departure_date}..{a.return_date} {a.price} {a.currency}")

        # --- Heartbeat + summary ---
        cheapest = [
            {"origin": r["origin"], "destination": r["destination"],
             "departure_date": r["departure_date"],
             "return_date": r["return_date"], "stay_days": r["stay_days"],
             "price": r["price"], "currency": r["currency"]}
            for r in db_mod.cheapest_recent_itineraries(
                conn, route.name, min_stay=route.stay.min_days,
                max_stay=route.stay.max_days, limit=5,
                earliest_departure=route.search_window.earliest_departure,
                latest_return=route.search_window.latest_return)
        ]
        status = "degraded" if degraded else "ok"
        summary = {
            "route": route.name,
            "trigger": trigger,
            "config_source": cfg_source,
            "started_at": started_at,
            "finished_at": _now_iso(),
            "plan": {k: v for k, v in plan.calls_by_source.items() if v},
            "plan_notes": list(plan.notes),
            "client_warnings": client_warnings,
            "results": results,
            "cheapest_top5": cheapest,
            "alerts_fired": [
                {"type": a.alert_type, "origin": a.origin,
                 "destination": a.destination,
                 "departure_date": a.departure_date,
                 "return_date": a.return_date,
                 "price": a.price, "currency": a.currency}
                for a in fired
            ],
            "status": status,
        }
        db_mod.insert_scan_run(
            conn,
            started_at=started_at,
            finished_at=summary["finished_at"],
            route_id=route.name,
            trigger=trigger,
            sources=",".join(sources),
            rows_stored=sum(r["stored"] for r in results.values()),
            alerts_fired=len(fired),
            status=status,
            summary_json=json.dumps(summary),
        )
        if args.json_summary:
            args.json_summary.write_text(json.dumps(summary, indent=2),
                                         encoding="utf-8")
            LOG.info("summary written to %s", args.json_summary)

    return EXIT_DEGRADED if degraded else EXIT_OK


def _run_verification(*, conn, route, candidates, plan_source, clients,
                      caps, run_followup):
    """Verify candidates via the planned source, falling back to serpapi.

    The fallback is the CI path: the plan may say googleflights, but on a
    runner without a browser the client is None (construction warning) or
    dies on the first query (GoogleFlightsUnavailable aborts the batch,
    itineraries_queried == 0). Either way the SAME candidate list runs
    through serpapi, capped to its budget — a Google block must never
    silently drop verification.

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
    if plan_source == "serpapi" or fallback is None:
        return 0, 0, plan_source, f"{plan_source} unavailable and no serpapi fallback"
    capped = candidates[: caps.serpapi] if caps.serpapi else candidates
    if len(capped) < len(candidates):
        LOG.info("serpapi fallback capped to %d of %d candidates",
                 len(capped), len(candidates))
    try:
        res = run_followup(conn=conn, client=fallback, route=route,
                           candidates=capped, skyscanner_max_calls=0)
        return res.rows_stored, res.itineraries_queried, "serpapi", None
    except Exception as exc:  # noqa: BLE001
        return 0, 0, "serpapi", str(exc)


if __name__ == "__main__":
    sys.exit(main())
