"""One-time migration: copy local data/tracker.db into Turso via HTTP.

Talks to Turso's HTTP API directly so it works on any Python version
(including 3.14 where libsql-experimental has no prebuilt wheel yet).
No extra Python packages needed beyond `requests`.

Prerequisites:
  1. Created a Turso DB via the web UI or `turso db create flight-tracker`.
  2. Added these to .env:
       TURSO_DATABASE_URL=libsql://flight-tracker-<your-org>.turso.io
       TURSO_AUTH_TOKEN=<long token>

Run:
    python migrate_to_turso.py

What it does:
  - Reads every row from local data/tracker.db.
  - Creates the schema on the remote Turso DB.
  - Bulk-inserts every row in 200-row batches via Turso's /v2/pipeline
    HTTP endpoint.

Safe to re-run, but ASSUME you've truncated the remote first if you do —
otherwise rows will duplicate (no PKs on most tables to dedup against).
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent
LOCAL_DB = REPO / "data" / "tracker.db"
BATCH_SIZE = 200


def main() -> int:
    load_dotenv(dotenv_path=REPO / ".env")
    turso_url = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
    turso_token = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
    if not (turso_url and turso_token):
        print("ERROR: Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN in .env first.",
              file=sys.stderr)
        return 1
    if not LOCAL_DB.exists():
        print(f"ERROR: No local DB at {LOCAL_DB}.", file=sys.stderr)
        return 1

    # libsql:// → https:// for the HTTP API.
    http_url = turso_url.replace("libsql://", "https://", 1)
    pipeline_url = f"{http_url}/v2/pipeline"
    headers = {
        "Authorization": f"Bearer {turso_token}",
        "Content-Type": "application/json",
    }

    print(f"source: {LOCAL_DB}")
    print(f"target: {http_url}")
    print()

    src = sqlite3.connect(LOCAL_DB)
    src.row_factory = sqlite3.Row

    # Quick health check.
    print("pinging Turso...")
    r = _post(pipeline_url, headers, [_stmt("SELECT 1")])
    if r is None:
        return 1
    print("  ok")

    # Read the schema from lib/db.py's SCHEMA constant + apply migrations.
    print("ensuring schema on remote...")
    from lib.db import SCHEMA, _MIGRATIONS
    # Split SCHEMA into individual statements (Turso pipeline expects one
    # statement per request slot). Comments and blank lines are ignored
    # by the SQL parser server-side; we still strip them client-side to
    # produce cleaner stmt list.
    stmts = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    requests_payload = [_stmt(s) for s in stmts]
    r = _post(pipeline_url, headers, requests_payload)
    if r is None:
        return 1
    print(f"  applied {len(stmts)} schema statements")
    # Apply migrations (they're conditional ALTER TABLEs — only run when
    # the column is missing, mirroring lib.db._apply_migrations).
    for table, column, ddl in _MIGRATIONS:
        # Check whether column exists on remote.
        check = _post(pipeline_url, headers,
                      [_stmt(f"PRAGMA table_info({table})")])
        if check is None:
            return 1
        cols = _extract_column_names_from_pragma(check)
        if column not in cols:
            r = _post(pipeline_url, headers, [_stmt(ddl)])
            if r is None:
                return 1
            print(f"  migration applied: {table}.{column}")

    # Copy each table's rows in batches.
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

        cols = [r[1] for r in src.execute(f"PRAGMA table_info({t})").fetchall()]
        col_csv = ", ".join(cols)
        placeholders = ", ".join("?" * len(cols))
        insert_sql = f"INSERT INTO {t} ({col_csv}) VALUES ({placeholders})"

        rows = list(src.execute(f"SELECT {col_csv} FROM {t}"))
        print(f"  {t}: copying {len(rows):,} rows", end="", flush=True)
        for i in range(0, len(rows), BATCH_SIZE):
            chunk = rows[i:i + BATCH_SIZE]
            batch_stmts = [
                _stmt(insert_sql, [_sqlite_value_to_turso(v) for v in row])
                for row in chunk
            ]
            resp = _post(pipeline_url, headers, batch_stmts)
            if resp is None:
                print()  # newline before error
                return 1
            print(".", end="", flush=True)
        print(f"  done ({count:,})")
        total += count

    print()
    print(f"Migration complete. {total:,} rows now live on Turso.")
    return 0


# --- helpers ---------------------------------------------------------------


def _stmt(sql: str, args: list | None = None) -> dict:
    """Build a single 'execute' request for Turso's pipeline format."""
    out: dict = {"type": "execute", "stmt": {"sql": sql}}
    if args is not None:
        out["stmt"]["args"] = args
    return out


def _sqlite_value_to_turso(v) -> dict:
    """Convert a Python value to Turso's typed argument format."""
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": float(v)}
    if isinstance(v, (bytes, bytearray)):
        import base64
        return {"type": "blob", "base64": base64.b64encode(v).decode("ascii")}
    return {"type": "text", "value": str(v)}


def _post(url: str, headers: dict, requests_payload: list) -> dict | None:
    """POST a pipeline payload; return parsed JSON or None on failure."""
    body = {"requests": requests_payload + [{"type": "close"}]}
    try:
        r = requests.post(url, headers=headers, data=json.dumps(body), timeout=60)
    except requests.RequestException as exc:
        print(f"\nERROR: network: {exc}", file=sys.stderr)
        return None
    if not r.ok:
        print(f"\nERROR: HTTP {r.status_code}: {r.text[:500]}", file=sys.stderr)
        return None
    try:
        data = r.json()
    except ValueError:
        print(f"\nERROR: non-JSON response: {r.text[:200]}", file=sys.stderr)
        return None
    # Look for per-result errors inside the pipeline response.
    for i, result in enumerate(data.get("results") or []):
        if result.get("type") == "error":
            err = result.get("error") or {}
            print(f"\nERROR: pipeline step {i}: {err.get('message')}", file=sys.stderr)
            return None
    return data


def _extract_column_names_from_pragma(pipeline_response: dict) -> set[str]:
    """Pull column-name strings out of a PRAGMA table_info() response."""
    out: set[str] = set()
    results = pipeline_response.get("results") or []
    if not results:
        return out
    first = results[0]
    if first.get("type") != "ok":
        return out
    rows = (first.get("response", {}).get("result", {}).get("rows") or [])
    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    for row in rows:
        if isinstance(row, list) and len(row) >= 2:
            name_cell = row[1]
            if isinstance(name_cell, dict) and "value" in name_cell:
                out.add(str(name_cell["value"]))
    return out


if __name__ == "__main__":
    sys.exit(main())
