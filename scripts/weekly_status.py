#!/usr/bin/env python
"""Weekly Monday status push (plan 2026-11-14, A6). See lib/weekly_status.py.

Usage:
    python scripts/weekly_status.py [--dry-run] [--now 2026-11-16T07:07:00Z]
                                    [--json-summary weekly.json]

Reads the database only (Turso in CI, data/tracker.db locally): no metered
calls and no writes. The owner search's config is parsed from its DB row
directly (not through route_store, whose self-heal can write). --dry-run
prints the push without sending it.

Exit codes: 0 built (and sent unless --dry-run); 1 could not build it (the
workflow's failure push fires).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="weekly status push")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--now", default=None,
                    help="build as of this UTC time (demonstrations)")
    ap.add_argument("--json-summary", default=None)
    args = ap.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=REPO / ".env")
    except ImportError:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    from lib import db as db_mod
    from lib import weekly_status as ws
    from lib.config import route_from_json
    from lib.pushes import push
    from lib.route_store import get_route_config_json

    now = (datetime.fromisoformat(args.now.replace("Z", "+00:00"))
           if args.now else datetime.now(timezone.utc))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        owner = conn.execute(
            "SELECT search_id FROM searches WHERE status = 'active' "
            "AND priority = 'owner' ORDER BY created_at LIMIT 1").fetchone()
        route = None
        if owner is not None:
            raw = get_route_config_json(conn, owner["search_id"])
            if raw is not None:
                # Resolve the rolling window as of the LAST scan, the dates
                # the radar shows and the push line promises ("as of the
                # last scan"); now only when the search never scanned.
                last = conn.execute(
                    "SELECT MAX(started_at) FROM scan_runs WHERE route_id = ? "
                    "AND status IN ('ok', 'degraded') AND started_at < ?",
                    (owner["search_id"], ws._iso(now))).fetchone()[0]
                as_of = (datetime.fromisoformat(
                    last.replace("Z", "+00:00")).date() if last else now.date())
                route = route_from_json(raw, today=as_of)
        status = ws.build(conn, route, now)

    print(("[DRY RUN: not sent]\n" if args.dry_run else "")
          + status.title + "\n" + status.body)
    sent = False
    if not args.dry_run:
        sent = push(status.title, status.body, priority="low",
                    tags="calendar")
        if not os.environ.get("NTFY_TOPIC", "").strip():
            print("NTFY_TOPIC not set — printed, not sent")

    if args.json_summary:
        Path(args.json_summary).write_text(json.dumps({
            "now": now.isoformat(), "dry_run": args.dry_run,
            "title": status.title, "body": status.body, "sent": sent,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        from lib.credentials import redact_network_error
        print("weekly status could not run: " + redact_network_error(
            exc, os.environ.get("TURSO_AUTH_TOKEN", "").strip() or None),
            file=sys.stderr)
        sys.exit(1)
