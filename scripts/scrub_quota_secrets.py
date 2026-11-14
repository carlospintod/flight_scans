#!/usr/bin/env python
"""Cut stored SerpAPI /account snapshots down to their safe fields.

Until 2026-11-14 run_batch stored SerpAPI's whole /account payload in
quota_snapshots.raw_json, and that payload carries the account's api_key,
email and id. This rewrites each serpapi row to the subset
lib/serpapi_io.safe_account_fields keeps (ACCOUNT_SAFE_FIELDS), which is
what new rows get. Only raw_json changes. checked_at, remaining and
limit_total stay as they are, so the ledger and /ops read the same numbers.

Dry run by default: it reads, counts and writes nothing. --apply does the
rewrite. Output is counts and field names only, never a value.

Usage: python scripts/scrub_quota_secrets.py [--apply] [--env-file PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib import db as db_mod  # noqa: E402
from lib.serpapi_io import safe_account_fields  # noqa: E402

SOURCE = "serpapi"
LOCAL_DB = REPO / "data" / "tracker.db"
# Must not appear anywhere in a serpapi row once --apply has run.
MARKERS = ("api_key", "account_email", "account_id")


@dataclass
class Report:
    scanned: int = 0        # serpapi rows that have a raw_json
    to_rewrite: int = 0     # rows carrying a field outside the allowlist
    rewritten: int = 0      # rows actually updated (--apply only)
    unparseable: int = 0    # not a JSON object: left alone, counted
    dropped: Counter = field(default_factory=Counter)   # field -> rows
    first_at: str | None = None   # checked_at range of the rows to rewrite
    last_at: str | None = None


def scrub(conn, *, apply: bool) -> Report:
    rep = Report()
    rows = conn.execute(
        "SELECT rowid AS rid, checked_at, raw_json FROM quota_snapshots "
        "WHERE source = ? AND raw_json IS NOT NULL ORDER BY rowid",
        (SOURCE,)).fetchall()
    for row in rows:
        rep.scanned += 1
        old = row["raw_json"]
        try:
            payload = json.loads(old)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            rep.unparseable += 1
            continue
        safe = safe_account_fields(payload)
        extra = set(payload) - set(safe)
        if not extra:
            continue
        rep.to_rewrite += 1
        rep.dropped.update(extra)
        at = str(row["checked_at"])
        rep.first_at = min(rep.first_at or at, at)
        rep.last_at = max(rep.last_at or at, at)
        if apply:
            # Compare-and-set on the text just read: a row that changed in
            # between is left for a re-run, never overwritten blind.
            cur = conn.execute(
                "UPDATE quota_snapshots SET raw_json = ? "
                "WHERE rowid = ? AND source = ? AND raw_json = ?",
                (json.dumps(safe), row["rid"], SOURCE, old))
            rep.rewritten += cur.rowcount or 0
    return rep


def leftovers(conn) -> dict[str, int]:
    """Per marker, the serpapi rows whose raw_json still contains it
    (an exact substring check, unparseable rows included)."""
    rows = conn.execute(
        "SELECT raw_json FROM quota_snapshots "
        "WHERE source = ? AND raw_json IS NOT NULL", (SOURCE,)).fetchall()
    return {m: sum(1 for r in rows if m in str(r["raw_json"])) for m in MARKERS}


def _turso_configured() -> bool:
    return bool((os.environ.get("TURSO_DATABASE_URL") or "").strip()
                and (os.environ.get("TURSO_AUTH_TOKEN") or "").strip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Cut stored SerpAPI /account snapshots down to their "
                    "safe fields (dry run unless --apply).")
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the rows (default: dry run, writes nothing)")
    ap.add_argument("--env-file", type=Path, default=REPO / ".env",
                    help="dotenv file holding TURSO_* (default: the repo's .env)")
    args = ap.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(dotenv_path=args.env_file)
    if _turso_configured():
        print("DB: Turso (production)")
    elif LOCAL_DB.exists():
        print(f"DB: local SQLite {LOCAL_DB}")
    else:
        print(f"no TURSO_* in the environment or {args.env_file}, and no "
              f"local DB at {LOCAL_DB}: nothing to scrub")
        return 2

    with db_mod.connect(LOCAL_DB) as conn:
        rep = scrub(conn, apply=args.apply)
        print(f"{SOURCE} snapshots with a raw_json: {rep.scanned}")
        print(f"  carrying fields outside the allowlist: {rep.to_rewrite}"
              + (f" (checked_at {rep.first_at} .. {rep.last_at})"
                 if rep.to_rewrite else ""))
        if rep.unparseable:
            print(f"  not a JSON object (left alone): {rep.unparseable}")
        for name, n in sorted(rep.dropped.items()):
            print(f"  drops {name}: {n} rows")
        if not args.apply:
            print(f"dry run: nothing written. --apply rewrites {rep.to_rewrite} rows.")
            return 0
        conn.commit()
        left = leftovers(conn)
        print(f"rewritten: {rep.rewritten} of {rep.to_rewrite}")
        for m, n in left.items():
            print(f"  rows still containing {m!r}: {n}")
        clean = rep.rewritten == rep.to_rewrite and not any(left.values())
        print("clean" if clean else "NOT clean: re-run, or check the rows above")
        return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
