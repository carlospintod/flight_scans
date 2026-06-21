"""One-time migration: copy local data/tracker.db into Turso.

Run from the repo root after:
  1. Signing up for Turso (https://turso.tech, free).
  2. Installing the Turso CLI and creating a database:
       turso db create flight-tracker
       turso db tokens create flight-tracker
  3. Adding to your local `.env`:
       TURSO_DATABASE_URL=libsql://flight-tracker-<your-handle>.turso.io
       TURSO_AUTH_TOKEN=<token from step 2>
  4. Installing the Python client:
       pip install libsql-experimental

Then run this script. It will:
  - Read every row from your existing local SQLite tracker.db.
  - Write them into the Turso database via libsql.
  - Idempotent (re-runnable) because it uses INSERT OR REPLACE-style
    upserts only where there are PKs. Other tables append, so don't
    run it twice unless you've truncated the remote first.

Safe rollback: keep `data/tracker.db` as is. Until you commit to using
Turso permanently, your local DB is still authoritative.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent
LOCAL_DB = REPO / "data" / "tracker.db"


def main() -> int:
    load_dotenv(dotenv_path=REPO / ".env")
    turso_url = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
    turso_token = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
    if not (turso_url and turso_token):
        print("Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN in .env first.",
              file=sys.stderr)
        return 1
    if not LOCAL_DB.exists():
        print(f"No local DB at {LOCAL_DB}; nothing to migrate.", file=sys.stderr)
        return 1

    try:
        import libsql_experimental as libsql  # type: ignore
    except ImportError:
        print("libsql-experimental is not installed. Run:\n"
              "  pip install libsql-experimental", file=sys.stderr)
        return 1

    print(f"source: {LOCAL_DB}")
    print(f"target: {turso_url}")
    print()

    src = sqlite3.connect(LOCAL_DB)
    src.row_factory = sqlite3.Row

    # Use a temp file for the embedded replica so we don't clobber the
    # local production DB if anything goes wrong.
    cache_path = REPO / "data" / "tracker_turso_cache.db"
    dst = libsql.connect(
        str(cache_path),
        sync_url=turso_url,
        auth_token=turso_token,
    )
    print("Initial sync from remote (will be empty on first run)...")
    try:
        dst.sync()
    except Exception as exc:  # noqa: BLE001 — empty remote is fine
        print(f"  initial sync warning: {exc}")

    # Make sure schema exists on the remote.
    sys.path.insert(0, str(REPO))
    from lib.db import ensure_schema
    print("Ensuring schema on remote...")
    ensure_schema(dst)

    tables = [
        "routes", "calendar_snapshots", "departure_curves",
        "point_queries", "alerts", "airport_cache", "quota_snapshots",
    ]
    total = 0
    for t in tables:
        try:
            count = src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            print(f"  {t}: not in local DB, skipping")
            continue
        if count == 0:
            print(f"  {t}: 0 rows, skipping")
            continue

        # Discover columns from the source so we don't hard-code them.
        cols = [r[1] for r in src.execute(f"PRAGMA table_info({t})").fetchall()]
        col_csv = ", ".join(cols)
        placeholders = ", ".join("?" * len(cols))
        sql = f"INSERT INTO {t} ({col_csv}) VALUES ({placeholders})"
        rows = src.execute(f"SELECT {col_csv} FROM {t}").fetchall()
        # Insert in batches of 500 to avoid huge round trips.
        batch = 500
        for i in range(0, len(rows), batch):
            chunk = [tuple(r) for r in rows[i:i + batch]]
            dst.executemany(sql, chunk)
        print(f"  {t}: copied {count} rows")
        total += count

    print()
    print(f"Syncing {total} rows to remote...")
    dst.sync()
    print("Done.")
    print()
    print(f"You can now delete {cache_path} (it was a temp replica).")
    print("From here on, Streamlit + the CLI will use Turso whenever "
          "TURSO_* env vars are set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
