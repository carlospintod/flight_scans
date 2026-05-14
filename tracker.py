"""CLI entry point for the flight tracker.

Subcommands wire up against `lib/` modules. Each subcommand is a thin
adapter that loads the route config, opens the DB, and dispatches to the
relevant lib module. No business logic lives here.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from lib import alerts as alerts_mod
from lib import config as config_mod
from lib import db as db_mod
from lib import followup as followup_mod
from lib import report as report_mod
from lib import sweep as sweep_mod
from lib.api import SearchApiClient

ROOT = Path(__file__).resolve().parent
ROUTES_DIR = ROOT / "routes"
DEFAULT_DB_PATH = ROOT / "data" / "tracker.db"
ALERTS_LOG_PATH = ROOT / "data" / "alerts.log"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _load_route(route_name: str) -> config_mod.RouteConfig:
    path = ROUTES_DIR / f"{route_name}.yaml"
    if not path.exists():
        raise SystemExit(f"route config not found: {path}")
    return config_mod.load_route(path)


def cmd_sweep(args: argparse.Namespace) -> int:
    route = _load_route(args.route)
    client = None if args.dry_run else SearchApiClient.from_env()
    with db_mod.connect(args.db) as conn:
        db_mod.ensure_schema(conn)
        db_mod.upsert_route(conn, route)
        result = sweep_mod.run_sweep(
            conn=conn,
            client=client,
            route=route,
            max_calls=args.max_calls,
            dry_run=args.dry_run,
        )
    print(
        f"sweep route={route.name} calls={result.calls_made} "
        f"entries={result.entries_stored}"
    )
    return 0


def cmd_followup(args: argparse.Namespace) -> int:
    route = _load_route(args.route)
    client = None if args.dry_run else SearchApiClient.from_env()
    with db_mod.connect(args.db) as conn:
        db_mod.ensure_schema(conn)
        db_mod.upsert_route(conn, route)
        result = followup_mod.run_followup(
            conn=conn,
            client=client,
            route=route,
            max_calls=args.max_calls,
            dry_run=args.dry_run,
        )
    print(
        f"followup route={route.name} calls={result.calls_made} "
        f"itineraries={result.itineraries_queried}"
    )
    return 0


def cmd_alerts(args: argparse.Namespace) -> int:
    route = _load_route(args.route)
    with db_mod.connect(args.db) as conn:
        db_mod.ensure_schema(conn)
        db_mod.upsert_route(conn, route)
        fired = alerts_mod.evaluate(
            conn=conn,
            route=route,
            log_path=ALERTS_LOG_PATH,
        )
    print(f"alerts route={route.name} fired={len(fired)}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    route = _load_route(args.route)
    with db_mod.connect(args.db) as conn:
        db_mod.ensure_schema(conn)
        report_mod.print_report(conn=conn, route=route, limit=args.limit)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tracker", description=__doc__)
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="path to SQLite DB")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sweep", help="Tier 1 calendar sweep across the search window")
    s.add_argument("--route", required=True)
    s.add_argument("--max-calls", type=int, default=None,
                   help="hard cap on API calls for this invocation")
    s.add_argument("--dry-run", action="store_true",
                   help="plan windows but do not call the API")
    s.set_defaults(func=cmd_sweep)

    f = sub.add_parser("followup", help="Tier 2 point queries on flagged itineraries")
    f.add_argument("--route", required=True)
    f.add_argument("--max-calls", type=int, default=None)
    f.add_argument("--dry-run", action="store_true")
    f.set_defaults(func=cmd_followup)

    a = sub.add_parser("alerts", help="evaluate alert conditions and log them")
    a.add_argument("--route", required=True)
    a.set_defaults(func=cmd_alerts)

    r = sub.add_parser("report", help="print recent alerts and cheapest itineraries")
    r.add_argument("--route", required=True)
    r.add_argument("--limit", type=int, default=20)
    r.set_defaults(func=cmd_report)

    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
