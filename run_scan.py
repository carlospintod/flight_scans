"""Headless scan runner — the CLI equivalent of the UI's RUN button.

Usage:
    python run_scan.py [--sources googleflights,aviasales,kiwi] [--cap 25]

Builds a RunPlan for the route's configured window and executes it:
Kiwi discovery bands (when quota allows), point verification via the
free Google Flights direct client, Aviasales bonus signal, then alert
evaluation (drop + new_low). Respects TURSO_* env vars — writes to the
same DB the deployed UI reads.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
load_dotenv(dotenv_path=REPO / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOG = logging.getLogger("scan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", default="spain-nairobi")
    ap.add_argument("--sources", default="googleflights,aviasales,kiwi")
    ap.add_argument("--cap", type=int, default=25,
                    help="max googleflights verifications this scan")
    args = ap.parse_args()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    from lib import alerts as alerts_mod
    from lib import db as db_mod
    from lib.config import load_route
    from lib.followup import run_followup
    from lib.planner import Caps, build_run_plan

    route = load_route(REPO / "routes" / f"{args.route}.yaml")

    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        db_mod.ensure_schema(conn)
        db_mod.upsert_route(conn, route)

        plan = build_run_plan(
            conn, route, sources=sources,
            caps=Caps(googleflights=args.cap, kiwi=20,
                      searchapi_sweep=0, searchapi_followup=0),
            today=date.today(),
        )
        print("\n=== PLAN ===")
        for k, v in plan.calls_by_source.items():
            if v:
                print(f"  {k}: {v} calls")
        for n in plan.notes:
            print(f"  note: {n}")

        # --- Kiwi discovery bands (fails gracefully while quota is 0) ---
        if plan.kiwi_bands and "kiwi" in sources:
            from lib.kiwi_rapidapi import KiwiClient
            from ui._common import _run_kiwi_discovery
            try:
                kw = KiwiClient.from_env(db_conn=conn)
                stored = _run_kiwi_discovery(
                    conn, kw, route, bands=list(plan.kiwi_bands), dry_run=False)
                print(f"\nkiwi discovery: {stored} itineraries stored")
            except Exception as exc:  # noqa: BLE001
                print(f"\nkiwi discovery unavailable: {exc}")

        # --- Free verification via Google Flights direct ---
        if plan.followup_source == "googleflights" and plan.followup_candidates:
            from lib.googleflights_direct import GoogleFlightsClient
            with GoogleFlightsClient.from_env() as gf:
                res = run_followup(
                    conn=conn, client=gf, route=route,
                    candidates=list(plan.followup_candidates),
                    skyscanner_max_calls=0,
                )
            print(f"\ngoogleflights verification: "
                  f"{res.itineraries_queried} itineraries, "
                  f"{res.rows_stored} rows, {res.empty_results} empty")

        # --- Aviasales bonus signal ---
        if plan.aviasales_pairs:
            from lib.aviasales_api import AviasalesClient
            from ui._common import _run_aviasales_sweep
            try:
                av = AviasalesClient.from_env()
                stored = _run_aviasales_sweep(
                    conn, av, route, dry_run=False,
                    pairs=list(plan.aviasales_pairs))
                print(f"aviasales: {stored} rows stored")
            except Exception as exc:  # noqa: BLE001
                print(f"aviasales unavailable: {exc}")

        # --- Alerts (drop + new_low) ---
        fired = alerts_mod.evaluate(
            conn=conn, route=route, log_path=REPO / "data" / "alerts.log")
        print(f"\nalerts fired: {len(fired)}")
        for a in fired:
            print(f"  [{a.alert_type}] {a.origin}->{a.destination} "
                  f"{a.departure_date}..{a.return_date} {a.price} {a.currency}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
