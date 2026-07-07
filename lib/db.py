"""SQLite storage layer.

Schema mirrors CLAUDE.md verbatim. This module owns:
  * connection / context manager
  * one-shot schema migration (CREATE TABLE IF NOT EXISTS)
  * row inserts for calendar snapshots, point queries, alerts
  * read queries used by alert evaluation and reporting

No business logic. The callers decide *what* to filter — this layer only
knows how to read and write.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .config import RouteConfig

LOG = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS routes (
    route_id     TEXT PRIMARY KEY,
    config_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_snapshots (
    snapshot_at      TEXT NOT NULL,
    route_id         TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'searchapi',
    origin           TEXT NOT NULL,
    destination      TEXT NOT NULL,
    departure_date   TEXT NOT NULL,
    return_date      TEXT NOT NULL,
    stay_days        INTEGER NOT NULL,
    price            INTEGER NOT NULL,
    currency         TEXT NOT NULL,
    is_lowest_price  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cal_itin
    ON calendar_snapshots (route_id, source, origin, destination, departure_date, return_date);
CREATE INDEX IF NOT EXISTS idx_cal_time
    ON calendar_snapshots (snapshot_at);

CREATE TABLE IF NOT EXISTS point_queries (
    snapshot_at      TEXT NOT NULL,
    route_id         TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'searchapi',
    origin           TEXT NOT NULL,
    destination      TEXT NOT NULL,
    departure_date   TEXT NOT NULL,
    return_date      TEXT NOT NULL,
    rank             INTEGER NOT NULL,
    price            INTEGER NOT NULL,
    currency         TEXT NOT NULL,
    carriers         TEXT NOT NULL,
    total_minutes    INTEGER,
    stops            INTEGER,
    is_self_transfer INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pq_itin
    ON point_queries (route_id, source, origin, destination, departure_date, return_date);
CREATE INDEX IF NOT EXISTS idx_pq_time
    ON point_queries (snapshot_at);

CREATE TABLE IF NOT EXISTS departure_curves (
    snapshot_at     TEXT NOT NULL,
    route_id        TEXT NOT NULL,
    source          TEXT NOT NULL,
    origin          TEXT NOT NULL,
    destination     TEXT NOT NULL,
    departure_date  TEXT NOT NULL,
    price           REAL NOT NULL,
    price_group     TEXT,
    currency        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_curve_lookup
    ON departure_curves (route_id, source, origin, destination, departure_date);
CREATE INDEX IF NOT EXISTS idx_curve_time
    ON departure_curves (snapshot_at);

CREATE TABLE IF NOT EXISTS airport_cache (
    iata_code      TEXT PRIMARY KEY,
    sky_id         TEXT NOT NULL,
    entity_id      TEXT NOT NULL,
    display_name   TEXT,
    looked_up_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quota_snapshots (
    checked_at   TEXT NOT NULL,
    source       TEXT NOT NULL,    -- 'searchapi' | 'skyscanner'
    remaining    INTEGER,           -- calls left in window
    limit_total  INTEGER,           -- total allowance (NULL when unknown)
    raw_json     TEXT               -- raw provider response for debugging
);

CREATE INDEX IF NOT EXISTS idx_quota_lookup
    ON quota_snapshots (source, checked_at);

CREATE TABLE IF NOT EXISTS alerts (
    fired_at         TEXT NOT NULL,
    route_id         TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'searchapi',
    origin           TEXT NOT NULL,
    destination      TEXT NOT NULL,
    departure_date   TEXT NOT NULL,
    return_date      TEXT NOT NULL,
    price            INTEGER NOT NULL,
    currency         TEXT NOT NULL,
    baseline_median  INTEGER NOT NULL,
    drop_pct         REAL NOT NULL,
    alert_type       TEXT NOT NULL DEFAULT 'drop'
);

-- One row per scan run (any trigger). This is the ops heartbeat: the
-- web app's stale-data badge and run history read from here. A bare
-- MAX(snapshot_at) can't tell "no scan ran" from "scan ran, stored 0".
CREATE TABLE IF NOT EXISTS scan_runs (
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    route_id      TEXT NOT NULL,
    trigger       TEXT NOT NULL,           -- 'cron' | 'dispatch' | 'local'
    sources       TEXT NOT NULL,           -- comma-joined as requested
    rows_stored   INTEGER NOT NULL DEFAULT 0,
    alerts_fired  INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,           -- 'ok' | 'degraded' | 'failed'
    summary_json  TEXT
);

CREATE INDEX IF NOT EXISTS idx_scan_runs_route
    ON scan_runs (route_id, started_at);

-- ============================================================================
-- Quota ledger (product plan M1+). In M1 these RECORD (shadow mode);
-- M2 turns on reservation enforcement. Design: predicted = guaranteed
-- upper bound; charge-before-call; ledger-primary, provider-re-anchored.
-- All correctness mutations are single-statement CAS (the Turso HTTP
-- backend autocommits per statement — no multi-statement transactions).
-- ============================================================================

-- Per-source configuration. Seeded with INSERT OR IGNORE (never upsert:
-- /ops edits — e.g. the $5 Kiwi Pro switch — must survive re-seeding).
CREATE TABLE IF NOT EXISTS quota_pools (
    source           TEXT PRIMARY KEY,
    pool_kind        TEXT NOT NULL,      -- 'monthly'|'per_run'|'rate_only'
    period_limit     INTEGER,            -- NULL for rate_only/per_run
    reset_anchor_day INTEGER,            -- display/pacing only; NEVER credits pools
    safety_margin    INTEGER NOT NULL DEFAULT 0,
    per_search_cap   INTEGER,            -- max units one search may reserve per run
    per_run_cap      INTEGER,            -- politeness budget for per_run pools
    active           INTEGER NOT NULL DEFAULT 1,
    updated_at       TEXT NOT NULL
);

-- Append-only spend log: ONE row per HTTP attempt, charged BEFORE the
-- call, never refunded (failed calls are metered by providers too).
CREATE TABLE IF NOT EXISTS spend_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT,
    search_id  TEXT,
    source     TEXT NOT NULL,
    units      INTEGER NOT NULL DEFAULT 1,
    op         TEXT NOT NULL,
    result     TEXT NOT NULL DEFAULT 'pending',  -- 'ok'|'error'|'429'|'empty'
    spent_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spend_source ON spend_events (source, event_id);
CREATE INDEX IF NOT EXISTS idx_spend_run ON spend_events (run_id, search_id, source);

-- Provider observations that re-anchor a pool's remaining. Ordered by
-- last_spend_event_id (monotonic), NOT wall-clock — every timestamp
-- helper in this repo truncates to seconds, so same-second events would
-- mis-bucket spend against the anchor (red-team A2). The latest anchor
-- per source is MAX(anchor_id).
CREATE TABLE IF NOT EXISTS pool_anchors (
    anchor_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT NOT NULL,
    baseline_remaining  INTEGER NOT NULL,
    limit_total         INTEGER,
    last_spend_event_id INTEGER NOT NULL DEFAULT 0,  -- spend AFTER this counts against the anchor
    origin              TEXT NOT NULL,   -- 'header'|'account_api'|'manual'|'reset_detected'|'seed'
    baseline_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_anchor_source ON pool_anchors (source, anchor_id);

-- One row per batch run: the lease + envelope. scan_runs stays as the
-- per-search execution heartbeat; this is the run-level wrapper.
CREATE TABLE IF NOT EXISTS ledger_runs (
    run_id           TEXT PRIMARY KEY,
    started_at       TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    finished_at      TEXT,
    trigger          TEXT NOT NULL,
    status           TEXT NOT NULL,      -- 'running'|'ok'|'degraded'|'failed'|'abandoned'
    planned_searches INTEGER NOT NULL DEFAULT 0,
    skipped_searches INTEGER NOT NULL DEFAULT 0,
    summary_json     TEXT
);

-- Reserved vs actual per (run, search, source, kind) — the accounting
-- table the "predicted <=X / used Y" digest line reads.
CREATE TABLE IF NOT EXISTS run_reservations (
    run_id         TEXT NOT NULL,
    search_id      TEXT NOT NULL,
    source         TEXT NOT NULL,
    kind           TEXT NOT NULL,        -- 'primary'|'contingency'
    reserved_units INTEGER NOT NULL,
    used_units     INTEGER NOT NULL DEFAULT 0,
    state          TEXT NOT NULL DEFAULT 'held',  -- 'held'|'consumed'|'released'|'skipped'
    skip_reason    TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resv_run ON run_reservations (run_id, search_id, source);
CREATE INDEX IF NOT EXISTS idx_resv_source_state ON run_reservations (source, state);
"""


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# Each tuple: (table, column, ddl_clause). Applied if the column is missing.
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("calendar_snapshots", "source",
     "ALTER TABLE calendar_snapshots ADD COLUMN source TEXT NOT NULL DEFAULT 'searchapi'"),
    ("point_queries", "source",
     "ALTER TABLE point_queries ADD COLUMN source TEXT NOT NULL DEFAULT 'searchapi'"),
    ("point_queries", "is_self_transfer",
     "ALTER TABLE point_queries ADD COLUMN is_self_transfer INTEGER NOT NULL DEFAULT 0"),
    ("alerts", "source",
     "ALTER TABLE alerts ADD COLUMN source TEXT NOT NULL DEFAULT 'searchapi'"),
    ("alerts", "alert_type",
     "ALTER TABLE alerts ADD COLUMN alert_type TEXT NOT NULL DEFAULT 'drop'"),
)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _MIGRATIONS:
        # Skip if the table doesn't exist yet (CREATE IF NOT EXISTS handles it).
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            continue
        if column in _existing_columns(conn, table):
            continue
        conn.execute(ddl)


@dataclass(frozen=True)
class CalendarRow:
    snapshot_at: str
    route_id: str
    source: str
    origin: str
    destination: str
    departure_date: str
    return_date: str
    stay_days: int
    price: int
    currency: str
    is_lowest_price: bool


@dataclass(frozen=True)
class PointRow:
    snapshot_at: str
    route_id: str
    source: str
    origin: str
    destination: str
    departure_date: str
    return_date: str
    rank: int
    price: int
    currency: str
    carriers: str
    total_minutes: int | None
    stops: int | None
    is_self_transfer: bool = False


@dataclass(frozen=True)
class CurveRow:
    snapshot_at: str
    route_id: str
    source: str
    origin: str
    destination: str
    departure_date: str
    price: float
    price_group: str | None
    currency: str


@dataclass(frozen=True)
class AlertRow:
    fired_at: str
    route_id: str
    source: str
    origin: str
    destination: str
    departure_date: str
    return_date: str
    price: int
    currency: str
    baseline_median: int
    drop_pct: float
    # 'drop' = below trailing median by threshold (needs >=4 obs);
    # 'new_low' = below previous all-time min (needs only 2 obs —
    # the alert mode that fits a near-in booking window).
    alert_type: str = "drop"


@contextmanager
def connect(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a DB connection. Caller is responsible for `ensure_schema`.

    Picks the connection backend based on env vars:

    * `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` set → use libSQL embedded
      replica that syncs to Turso. The local `path` becomes a cache
      file; writes propagate to the remote, reads come from the local
      replica. This is the Streamlit Cloud / production path: the
      ephemeral filesystem doesn't matter because data lives on Turso.
    * Neither set → plain sqlite3 against the local file. This is the
      local development path and what the existing tests exercise.

    The libSQL Python client exposes a sqlite3-compatible interface
    (cursor, row_factory, execute, executemany, executescript). It does
    NOT support sqlite3.Row, but it does support a tuple/dict cursor
    description we can use to make rows subscriptable by column name.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    turso_url = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
    turso_token = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
    if turso_url and turso_token:
        LOG.info("connecting via Turso HTTP API → %s", turso_url)
        from . import turso_http
        conn = turso_http.connect(turso_url, turso_token)
        conn.row_factory = sqlite3.Row  # opts into TursoRow (dict + tuple access)
        try:
            yield conn
        finally:
            conn.close()
        return

    # Local SQLite path.
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit; we manage tx
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply migrations, then create missing tables + indexes.

    Order matters: the new indexes reference the `source` column, which
    legacy DBs don't have yet. We run ALTER TABLE first so the indexes
    can be created over a column that exists.

    Legacy rows get their `source` defaulted to 'searchapi' via the
    ALTER ... DEFAULT clause (SQLite backfills automatically).
    """
    _apply_migrations(conn)
    conn.executescript(SCHEMA)


def upsert_route(conn: sqlite3.Connection, route: RouteConfig) -> None:
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO routes (route_id, config_json, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(route_id) DO UPDATE SET
            config_json = excluded.config_json,
            updated_at  = excluded.updated_at
        """,
        (route.name, route.to_json(), now, now),
    )


def insert_calendar_rows(conn: sqlite3.Connection, rows: Iterable[CalendarRow]) -> int:
    payload = [
        (
            r.snapshot_at, r.route_id, r.source, r.origin, r.destination,
            r.departure_date, r.return_date, r.stay_days,
            r.price, r.currency, 1 if r.is_lowest_price else 0,
        )
        for r in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO calendar_snapshots
            (snapshot_at, route_id, source, origin, destination,
             departure_date, return_date, stay_days,
             price, currency, is_lowest_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def insert_point_rows(conn: sqlite3.Connection, rows: Iterable[PointRow]) -> int:
    payload = [
        (
            r.snapshot_at, r.route_id, r.source, r.origin, r.destination,
            r.departure_date, r.return_date, r.rank,
            r.price, r.currency, r.carriers, r.total_minutes, r.stops,
            1 if r.is_self_transfer else 0,
        )
        for r in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO point_queries
            (snapshot_at, route_id, source, origin, destination,
             departure_date, return_date, rank,
             price, currency, carriers, total_minutes, stops, is_self_transfer)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def insert_curve_rows(conn: sqlite3.Connection, rows: Iterable[CurveRow]) -> int:
    payload = [
        (
            r.snapshot_at, r.route_id, r.source, r.origin, r.destination,
            r.departure_date, r.price, r.price_group, r.currency,
        )
        for r in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO departure_curves
            (snapshot_at, route_id, source, origin, destination,
             departure_date, price, price_group, currency)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def insert_alert_rows(conn: sqlite3.Connection, rows: Iterable[AlertRow]) -> int:
    payload = [
        (
            r.fired_at, r.route_id, r.source, r.origin, r.destination,
            r.departure_date, r.return_date,
            r.price, r.currency, r.baseline_median, r.drop_pct,
            r.alert_type,
        )
        for r in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO alerts
            (fired_at, route_id, source, origin, destination,
             departure_date, return_date,
             price, currency, baseline_median, drop_pct, alert_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def lookup_airport(conn: sqlite3.Connection, iata: str) -> tuple[str, str] | None:
    """Return (sky_id, entity_id) from the cache, or None if not cached."""
    row = conn.execute(
        "SELECT sky_id, entity_id FROM airport_cache WHERE iata_code = ?",
        (iata,),
    ).fetchone()
    return (row["sky_id"], row["entity_id"]) if row else None


def store_airport(
    conn: sqlite3.Connection, iata: str, sky_id: str, entity_id: str,
    display_name: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO airport_cache (iata_code, sky_id, entity_id, display_name, looked_up_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(iata_code) DO UPDATE SET
            sky_id = excluded.sky_id,
            entity_id = excluded.entity_id,
            display_name = excluded.display_name,
            looked_up_at = excluded.looked_up_at
        """,
        (iata, sky_id, entity_id, display_name, _now_iso()),
    )


def record_quota(
    conn: sqlite3.Connection, *,
    source: str,
    remaining: int | None,
    limit_total: int | None = None,
    raw_json: str | None = None,
) -> None:
    """Insert a quota snapshot.

    Append-only; each call writes a new row. Callers should not write
    snapshots more than once per second per source — store the latest
    observation, not every poll.
    """
    conn.execute(
        """
        INSERT INTO quota_snapshots (checked_at, source, remaining, limit_total, raw_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (_now_iso(), source, remaining, limit_total, raw_json),
    )


def latest_quota(
    conn: sqlite3.Connection, *, source: str,
) -> sqlite3.Row | None:
    """Return the most recent quota snapshot for a source, or None."""
    return conn.execute(
        """
        SELECT * FROM quota_snapshots
        WHERE source = ?
        ORDER BY checked_at DESC
        LIMIT 1
        """,
        (source,),
    ).fetchone()


def insert_scan_run(
    conn: sqlite3.Connection, *,
    started_at: str,
    finished_at: str | None,
    route_id: str,
    trigger: str,
    sources: str,
    rows_stored: int,
    alerts_fired: int,
    status: str,
    summary_json: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO scan_runs (started_at, finished_at, route_id, trigger,
                               sources, rows_stored, alerts_fired, status,
                               summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (started_at, finished_at, route_id, trigger, sources,
         rows_stored, alerts_fired, status, summary_json),
    )


def latest_scan_run(
    conn: sqlite3.Connection, route_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM scan_runs
        WHERE route_id = ?
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (route_id,),
    ).fetchone()


def latest_calendar_snapshot_per_itinerary(
    conn: sqlite3.Connection, route_id: str, *, source: str | None = None,
) -> list[sqlite3.Row]:
    """Most recent calendar row for each itinerary in this route.

    With `source=None`, returns one row per (origin, destination,
    departure_date, return_date, source). With `source='X'`, returns one
    row per itinerary tuple, restricted to that source.
    """
    if source is None:
        return list(conn.execute(
            """
            SELECT cs.*
            FROM calendar_snapshots cs
            JOIN (
                SELECT source, origin, destination, departure_date, return_date,
                       MAX(snapshot_at) AS latest
                FROM calendar_snapshots
                WHERE route_id = ?
                GROUP BY source, origin, destination, departure_date, return_date
            ) m ON m.source = cs.source
               AND m.origin = cs.origin
               AND m.destination = cs.destination
               AND m.departure_date = cs.departure_date
               AND m.return_date = cs.return_date
               AND m.latest = cs.snapshot_at
            WHERE cs.route_id = ?
            """,
            (route_id, route_id),
        ))
    return list(conn.execute(
        """
        SELECT cs.*
        FROM calendar_snapshots cs
        JOIN (
            SELECT origin, destination, departure_date, return_date,
                   MAX(snapshot_at) AS latest
            FROM calendar_snapshots
            WHERE route_id = ? AND source = ?
            GROUP BY origin, destination, departure_date, return_date
        ) m ON m.origin = cs.origin
           AND m.destination = cs.destination
           AND m.departure_date = cs.departure_date
           AND m.return_date = cs.return_date
           AND m.latest = cs.snapshot_at
        WHERE cs.route_id = ? AND cs.source = ?
        """,
        (route_id, source, route_id, source),
    ))


def calendar_history_for_itinerary(
    conn: sqlite3.Connection,
    route_id: str,
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str,
    *,
    since: date | None = None,
    source: str | None = None,
) -> list[sqlite3.Row]:
    """Return ordered (oldest-first) calendar rows for one itinerary.

    Optional source filter — useful for per-source baselines.
    """
    params: list[object] = [route_id, origin, destination, departure_date, return_date]
    sql = (
        "SELECT * FROM calendar_snapshots "
        "WHERE route_id = ? AND origin = ? AND destination = ? "
        "AND departure_date = ? AND return_date = ?"
    )
    if source is not None:
        sql += " AND source = ?"
        params.append(source)
    if since is not None:
        sql += " AND snapshot_at >= ?"
        params.append(since.isoformat())
    sql += " ORDER BY snapshot_at ASC"
    return list(conn.execute(sql, params))


def recent_alerts(
    conn: sqlite3.Connection, route_id: str, *, limit: int = 20
) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM alerts WHERE route_id = ? ORDER BY fired_at DESC LIMIT ?",
        (route_id, limit),
    ))


def cheapest_recent_itineraries(
    conn: sqlite3.Connection,
    route_id: str,
    *,
    min_stay: int,
    max_stay: int,
    since: date | None = None,
    limit: int = 20,
    source: str | None = None,
    earliest_departure: date | None = None,
    latest_return: date | None = None,
) -> list[sqlite3.Row]:
    """Cheapest most-recent prices, filtered by stay range.

    Pass the route's `earliest_departure`/`latest_return` to keep results
    inside the CURRENT search window — the DB keeps out-of-window history
    by design, and a "cheapest" the user can't book is noise (a Sep 1
    departure topped the summary of a Sep 12+ window, 2026-07-06).
    """
    sql = (
        "SELECT cs.* FROM calendar_snapshots cs "
        "JOIN (SELECT source, origin, destination, departure_date, return_date, "
        "             MAX(snapshot_at) AS latest "
        "      FROM calendar_snapshots WHERE route_id = ? "
        "      GROUP BY source, origin, destination, departure_date, return_date) m "
        "  ON m.source = cs.source AND m.origin = cs.origin "
        "  AND m.destination = cs.destination "
        "  AND m.departure_date = cs.departure_date "
        "  AND m.return_date = cs.return_date "
        "  AND m.latest = cs.snapshot_at "
        "WHERE cs.route_id = ? AND cs.stay_days BETWEEN ? AND ?"
    )
    params: list[object] = [route_id, route_id, min_stay, max_stay]
    if source is not None:
        sql += " AND cs.source = ?"
        params.append(source)
    if since is not None:
        sql += " AND cs.snapshot_at >= ?"
        params.append(since.isoformat())
    if earliest_departure is not None:
        sql += " AND cs.departure_date >= ?"
        params.append(earliest_departure.isoformat())
    if latest_return is not None:
        sql += " AND cs.return_date <= ?"
        params.append(latest_return.isoformat())
    sql += " ORDER BY cs.price ASC LIMIT ?"
    params.append(limit)
    return list(conn.execute(sql, params))


def latest_curve(
    conn: sqlite3.Connection,
    route_id: str,
    *,
    origin: str,
    destination: str,
    source: str,
) -> list[sqlite3.Row]:
    """Return the latest snapshot's departure-curve rows for one origin/dest."""
    return list(conn.execute(
        """
        SELECT * FROM departure_curves
        WHERE route_id = ?
          AND source = ?
          AND origin = ?
          AND destination = ?
          AND snapshot_at = (
              SELECT MAX(snapshot_at) FROM departure_curves
              WHERE route_id = ? AND source = ?
                AND origin = ? AND destination = ?
          )
        ORDER BY departure_date ASC
        """,
        (route_id, source, origin, destination, route_id, source, origin, destination),
    ))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
