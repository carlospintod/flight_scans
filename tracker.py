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
from lib.searchapi_io import SearchApiClient
from lib.skyscanner_rapidapi import SkyScrapperClient

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


def _make_clients(dry_run: bool, db_conn, *, sources: set[str]):
    """Construct the API clients we need. None means 'don't use this source'."""
    sa_client = None
    sky_client = None
    if not dry_run:
        if "searchapi" in sources:
            sa_client = SearchApiClient.from_env()
        if "skyscanner" in sources:
            sky_client = SkyScrapperClient.from_env(db_conn=db_conn)
    return sa_client, sky_client


def cmd_sweep(args: argparse.Namespace) -> int:
    route = _load_route(args.route)
    requested = set(args.sources)
    with db_mod.connect(args.db) as conn:
        db_mod.ensure_schema(conn)
        db_mod.upsert_route(conn, route)
        sa_client, sky_client = _make_clients(args.dry_run, conn, sources=requested)
        result = sweep_mod.run_sweep(
            conn=conn,
            client=sa_client,
            route=route,
            max_calls=args.max_calls,
            dry_run=args.dry_run,
            skyscanner_client=sky_client,
            skyscanner_planned="skyscanner" in requested,
        )
    print(
        f"sweep route={route.name} "
        f"searchapi_calls={result.calls_made} grid_rows={result.entries_stored} "
        f"skyscanner_calls={result.curve_calls_made} "
        f"curve_rows={result.curve_entries_stored}"
    )
    return 0


def cmd_followup(args: argparse.Namespace) -> int:
    route = _load_route(args.route)
    requested = set(args.sources)
    with db_mod.connect(args.db) as conn:
        db_mod.ensure_schema(conn)
        db_mod.upsert_route(conn, route)
        sa_client, sky_client = _make_clients(args.dry_run, conn, sources=requested)
        result = followup_mod.run_followup(
            conn=conn,
            client=sa_client,
            route=route,
            max_calls=args.max_calls,
            dry_run=args.dry_run,
            skyscanner_client=sky_client,
        )
    print(
        f"followup route={route.name} "
        f"searchapi_calls={result.calls_made} "
        f"skyscanner_calls={result.skyscanner_calls} "
        f"itineraries_searchapi={result.itineraries_queried} "
        f"rows_stored={result.rows_stored}"
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
                   help="hard cap on SearchAPI grid calls for this invocation")
    s.add_argument("--dry-run", action="store_true",
                   help="plan windows but do not call the API")
    s.add_argument("--sources", nargs="+", default=["searchapi", "skyscanner"],
                   choices=["searchapi", "skyscanner"],
                   help="which data sources to query")
    s.set_defaults(func=cmd_sweep)

    f = sub.add_parser("followup", help="Tier 2 point queries on flagged itineraries")
    f.add_argument("--route", required=True)
    f.add_argument("--max-calls", type=int, default=None)
    f.add_argument("--dry-run", action="store_true")
    f.add_argument("--sources", nargs="+", default=["searchapi", "skyscanner"],
                   choices=["searchapi", "skyscanner"],
                   help="which data sources to query")
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
