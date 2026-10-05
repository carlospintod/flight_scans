#!/usr/bin/env python
"""Push a route's YAML into the live DB, and optionally pause one-way
searches to a destination — the no-login path for owner edits.

The DB row normally wins over the YAML seed (lib/route_store). This
script is the deliberate exception: the committed YAML becomes the
effective config. Run from the `apply-route` workflow (Turso secrets),
or locally against SQLite.

Usage: python scripts/apply_route.py --route spain-nairobi
           [--pause-one-way-to NBO] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib import db as db_mod  # noqa: E402
from lib.config import ConfigError, load_route, route_from_json  # noqa: E402
from lib.route_store import get_route_config_json, save_route_config  # noqa: E402


def _summary(route) -> str:
    sw = route.search_window
    return (f"{route.trip_type} {','.join(route.origins)}->"
            f"{','.join(route.destinations)} "
            f"{sw.earliest_departure}..{sw.latest_return} "
            f"stay {route.stay.min_days}-{route.stay.max_days}d")


def apply(conn, route_id: str, routes_dir: Path, *,
          pause_one_way_to: str | None, dry_run: bool) -> list[str]:
    """Returns the search_ids that were (or would be) paused."""
    new = load_route(routes_dir / f"{route_id}.yaml")
    raw = get_route_config_json(conn, route_id)
    if raw is None:
        print(f"{route_id}: no DB row yet")
    else:
        try:
            print(f"{route_id} DB  : {_summary(route_from_json(raw))}")
        except ConfigError as exc:
            print(f"{route_id} DB  : unparseable ({exc})")
    print(f"{route_id} YAML: {_summary(new)}")
    if not dry_run:
        save_route_config(conn, new)
        print(f"{route_id}: YAML applied to DB")

    paused: list[str] = []
    if pause_one_way_to:
        dest = pause_one_way_to.upper()
        rows = conn.execute(
            "SELECT s.search_id, r.config_json FROM searches s "
            "JOIN routes r ON r.route_id = s.search_id "
            "WHERE s.status = 'active'").fetchall()
        for row in rows:
            try:
                r = route_from_json(row[1])
            except ConfigError:
                continue
            if r.is_one_way and dest in r.destinations:
                paused.append(row[0])
                print(f"pause {row[0]}: {_summary(r)}")
        if not paused:
            print(f"no active one-way searches to {dest}")
        if paused and not dry_run:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for sid in paused:
                conn.execute(
                    "UPDATE searches SET status = 'paused', updated_at = ? "
                    "WHERE search_id = ? AND status = 'active'", (now, sid))
            print(f"paused {len(paused)} search(es)")
    if dry_run:
        print("dry run: nothing written")
    return paused


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", required=True)
    ap.add_argument("--pause-one-way-to", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        db_mod.ensure_schema(conn)
        apply(conn, args.route, REPO / "routes",
              pause_one_way_to=args.pause_one_way_to, dry_run=args.dry_run)
        conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
