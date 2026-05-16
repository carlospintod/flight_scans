"""Test the DB migration path: an old-shape DB should accept ensure_schema()
and have `source` defaulted on legacy rows.
"""

import sqlite3
from pathlib import Path

from lib.db import ensure_schema


def _make_old_db(path: Path) -> None:
    """Create a DB with the pre-multisource schema and seed one legacy row."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE calendar_snapshots (
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
        CREATE TABLE point_queries (
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
        CREATE TABLE alerts (
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
    )
    conn.execute(
        "INSERT INTO calendar_snapshots VALUES "
        "('2026-05-01T00:00:00Z', 'r', 'MAD', 'NBO', "
        " '2026-09-05', '2026-11-08', 64, 720, 'EUR', 0)"
    )
    conn.commit()
    conn.close()


def test_legacy_db_migrates_cleanly(tmp_path: Path):
    db = tmp_path / "legacy.db"
    _make_old_db(db)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    # Legacy row is still there, with source defaulted.
    rows = list(conn.execute("SELECT * FROM calendar_snapshots"))
    assert len(rows) == 1
    assert rows[0]["source"] == "searchapi"
    assert rows[0]["price"] == 720

    # New tables exist.
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "departure_curves" in tables
    assert "airport_cache" in tables

    # New columns exist.
    point_cols = {r[1] for r in conn.execute("PRAGMA table_info(point_queries)")}
    assert "source" in point_cols
    assert "is_self_transfer" in point_cols
    conn.close()


def test_fresh_db_picks_up_new_schema(tmp_path: Path):
    db = tmp_path / "fresh.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"calendar_snapshots", "point_queries", "departure_curves",
            "airport_cache", "alerts", "routes"} <= tables
    conn.close()
