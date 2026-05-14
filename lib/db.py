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

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .config import RouteConfig

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
    ON calendar_snapshots (route_id, origin, destination, departure_date, return_date);
CREATE INDEX IF NOT EXISTS idx_cal_time
    ON calendar_snapshots (snapshot_at);

CREATE TABLE IF NOT EXISTS point_queries (
    snapshot_at      TEXT NOT NULL,
    route_id         TEXT NOT NULL,
    origin           TEXT NOT NULL,
    destination      TEXT NOT NULL,
    departure_date   TEXT NOT NULL,
    return_date      TEXT NOT NULL,
    rank             INTEGER NOT NULL,
    price            INTEGER NOT NULL,
    currency         TEXT NOT NULL,
    carriers         TEXT NOT NULL,
    total_minutes    INTEGER,
    stops            INTEGER
);

CREATE INDEX IF NOT EXISTS idx_pq_itin
    ON point_queries (route_id, origin, destination, departure_date, return_date);
CREATE INDEX IF NOT EXISTS idx_pq_time
    ON point_queries (snapshot_at);

CREATE TABLE IF NOT EXISTS alerts (
    fired_at         TEXT NOT NULL,
    route_id         TEXT NOT NULL,
    origin           TEXT NOT NULL,
    destination      TEXT NOT NULL,
    departure_date   TEXT NOT NULL,
    return_date      TEXT NOT NULL,
    price            INTEGER NOT NULL,
    currency         TEXT NOT NULL,
    baseline_median  INTEGER NOT NULL,
    drop_pct         REAL NOT NULL
);
"""


@dataclass(frozen=True)
class CalendarRow:
    snapshot_at: str
    route_id: str
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


@dataclass(frozen=True)
class AlertRow:
    fired_at: str
    route_id: str
    origin: str
    destination: str
    departure_date: str
    return_date: str
    price: int
    currency: str
    baseline_median: int
    drop_pct: float


@contextmanager
def connect(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection. Caller is responsible for `ensure_schema`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit; we manage tx explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
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
            r.snapshot_at, r.route_id, r.origin, r.destination,
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
            (snapshot_at, route_id, origin, destination,
             departure_date, return_date, stay_days,
             price, currency, is_lowest_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def insert_point_rows(conn: sqlite3.Connection, rows: Iterable[PointRow]) -> int:
    payload = [
        (
            r.snapshot_at, r.route_id, r.origin, r.destination,
            r.departure_date, r.return_date, r.rank,
            r.price, r.currency, r.carriers, r.total_minutes, r.stops,
        )
        for r in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO point_queries
            (snapshot_at, route_id, origin, destination,
             departure_date, return_date, rank,
             price, currency, carriers, total_minutes, stops)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def insert_alert_rows(conn: sqlite3.Connection, rows: Iterable[AlertRow]) -> int:
    payload = [
        (
            r.fired_at, r.route_id, r.origin, r.destination,
            r.departure_date, r.return_date,
            r.price, r.currency, r.baseline_median, r.drop_pct,
        )
        for r in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO alerts
            (fired_at, route_id, origin, destination,
             departure_date, return_date,
             price, currency, baseline_median, drop_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def latest_calendar_snapshot_per_itinerary(
    conn: sqlite3.Connection, route_id: str
) -> list[sqlite3.Row]:
    """Most recent calendar row for each itinerary in this route.

    Used by followup to pick candidate itineraries to point-query.
    """
    return list(conn.execute(
        """
        SELECT cs.*
        FROM calendar_snapshots cs
        JOIN (
            SELECT origin, destination, departure_date, return_date,
                   MAX(snapshot_at) AS latest
            FROM calendar_snapshots
            WHERE route_id = ?
            GROUP BY origin, destination, departure_date, return_date
        ) m ON m.origin = cs.origin
           AND m.destination = cs.destination
           AND m.departure_date = cs.departure_date
           AND m.return_date = cs.return_date
           AND m.latest = cs.snapshot_at
        WHERE cs.route_id = ?
        """,
        (route_id, route_id),
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
) -> list[sqlite3.Row]:
    """Return ordered (oldest-first) calendar rows for one itinerary."""
    params: list[object] = [route_id, origin, destination, departure_date, return_date]
    sql = (
        "SELECT * FROM calendar_snapshots "
        "WHERE route_id = ? AND origin = ? AND destination = ? "
        "AND departure_date = ? AND return_date = ?"
    )
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
) -> list[sqlite3.Row]:
    """Cheapest most-recent prices, filtered by stay range."""
    sql = (
        "SELECT cs.* FROM calendar_snapshots cs "
        "JOIN (SELECT origin, destination, departure_date, return_date, "
        "             MAX(snapshot_at) AS latest "
        "      FROM calendar_snapshots WHERE route_id = ? "
        "      GROUP BY origin, destination, departure_date, return_date) m "
        "  ON m.origin = cs.origin AND m.destination = cs.destination "
        "  AND m.departure_date = cs.departure_date "
        "  AND m.return_date = cs.return_date "
        "  AND m.latest = cs.snapshot_at "
        "WHERE cs.route_id = ? AND cs.stay_days BETWEEN ? AND ?"
    )
    params: list[object] = [route_id, route_id, min_stay, max_stay]
    if since is not None:
        sql += " AND cs.snapshot_at >= ?"
        params.append(since.isoformat())
    sql += " ORDER BY cs.price ASC LIMIT ?"
    params.append(limit)
    return list(conn.execute(sql, params))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
