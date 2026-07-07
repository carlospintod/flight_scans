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

    from lib import db as db_mod
    from lib import route_store
    from lib.clients import make_clients
    from lib.planner import Caps, build_run_plan
    from lib.quota import POOL_SEEDS, QuotaLedger
    from lib.runner import execute_search

    started_at = _now_iso()

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

        # Quota ledger in SHADOW mode (M1): records every metered call as
        # a spend event and anchors pools from provider headers, but
        # never refuses — scan behavior is identical to unguarded.
        # Enforcement (reservations, skip-and-notify) arrives in M2.
        ledger = QuotaLedger(conn)
        ledger.seed_pools()
        for seed in POOL_SEEDS:
            ledger.seed_anchor_from_snapshots(seed[0])
        ledger_run_id = ledger.begin_shadow_run(trigger=trigger)

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

        clients, client_warnings = make_clients(
            sources, conn, ledger=ledger, run_id=ledger_run_id,
            search_id=args.route)
        for w in client_warnings:
            LOG.warning("%s", w)   # graceful skip, not degradation

        res = execute_search(conn=conn, route=route, plan=plan,
                             clients=clients, caps=caps,
                             alerts_log=REPO / "data" / "alerts.log")
        degraded = res.degraded
        results = res.results
        fired = res.alerts
        for source, r in results.items():
            line = f"{source}: attempted={r['attempted']} stored={r['stored']}"
            if r["error"]:
                line += f" ERROR={r['error'][:120]}"
            print(line)
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
        # Shadow-ledger close-out: promote provider headers observed
        # during this run into pool anchors, and put the recorded spend
        # next to the plan in the summary — the M1 drift report.
        try:
            ledger.capture_anchors_from_snapshots(started_at)
            ledger.finalize_run(ledger_run_id, status)
            shadow_spend = {
                f"{sid or '?'}:{src}": units
                for (sid, src), units in ledger.spent_by_run(ledger_run_id).items()
            }
            pool_view = {
                p.source: {"available": p.effective_available,
                           "provider_view": p.provider_view,
                           "anchored": p.baseline_at}
                for p in ledger.all_pool_states()
            }
        except Exception as exc:  # noqa: BLE001 — shadow mode must never fail a scan
            LOG.warning("shadow ledger close-out failed: %s", exc)
            shadow_spend, pool_view = {}, {}
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
            "ledger_shadow": {"run_id": ledger_run_id,
                              "spend": shadow_spend, "pools": pool_view},
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


if __name__ == "__main__":
    sys.exit(main())
