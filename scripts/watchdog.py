#!/usr/bin/env python
"""Dead-man watchdog: push when a scheduled job has silently stopped.

Usage:
    python scripts/watchdog.py [--dry-run] [--now 2026-11-20T19:00:00Z]
                               [--json-summary watchdog.json]

Reads the database only (Turso in CI, data/tracker.db locally): no
metered calls, no provider calls. See lib/watchdog.py for the checks.

--dry-run prints the pushes it WOULD send and leaves the state table
untouched, so a forced-stale demonstration (--now in the future) cannot
spam the topic or move the state machine.

Exit codes: 0 checked (bad findings are pushes, not job failures);
1 the watchdog itself could not run (the workflow's failure push fires).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

LOG = logging.getLogger("watchdog")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dead-man watchdog")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--now", default=None,
                    help="evaluate as of this UTC time (demonstrations)")
    ap.add_argument("--json-summary", default=None)
    args = ap.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=REPO / ".env")
    except ImportError:
        pass
    logging.basicConfig(level=logging.WARNING)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    from lib import db as db_mod
    from lib import watchdog as wd
    from lib.pushes import push

    now = (datetime.fromisoformat(args.now.replace("Z", "+00:00"))
           if args.now else datetime.now(timezone.utc))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        if not args.dry_run:
            # A dry run is read-only: it creates nothing either.
            db_mod.ensure_schema(conn)
            from lib import deals_db
            deals_db.ensure_deals_schema(conn)
        findings = wd.evaluate(conn, now)
        pushes, rows = wd.plan_pushes(conn, findings, now)

        print(f"watchdog as of {now.isoformat()}"
              + ("  [DRY RUN: nothing sent, state unchanged]"
                 if args.dry_run else ""))
        for f in findings:
            print(f"  {'BAD' if f.bad else 'ok '}  {f.check_id:<18} {f.detail}")
        sent = 0
        for p in pushes:
            print(f"\n  push [{p.priority}] {p.title}\n    {p.body}")
            if not args.dry_run:
                sent += 1 if push(p.title, p.body, priority=p.priority,
                                  tags=p.tags) else 0
        if not pushes:
            print("\n  no push (no transition, nothing due for a repeat)")
        if not args.dry_run:
            wd.save_state(conn, rows)
            if pushes and not os.environ.get("NTFY_TOPIC", "").strip():
                print("  NTFY_TOPIC not set — pushes printed, not sent")

    if args.json_summary:
        Path(args.json_summary).write_text(json.dumps({
            "now": now.isoformat(), "dry_run": args.dry_run,
            "findings": [{"check": f.check_id, "bad": f.bad,
                          "detail": f.detail} for f in findings],
            "pushes": [{"title": p.title, "body": p.body,
                        "priority": p.priority} for p in pushes],
            "sent": 0 if args.dry_run else sent,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        from lib.credentials import redact_network_error
        # A Turso URL error can carry the token in its text.
        print("watchdog could not run: " + redact_network_error(
            exc, os.environ.get("TURSO_AUTH_TOKEN", "").strip() or None),
            file=sys.stderr)
        sys.exit(1)
