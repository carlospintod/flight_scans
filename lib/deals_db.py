"""Vuelazo deal-pipeline storage (MVP-SPEC §2).

Additions layered on the existing lib/db.py schema — same conventions:
CREATE TABLE IF NOT EXISTS, no FOREIGN KEY clauses (the Turso HTTP path
never enforces them), single-statement correctness mutations (autocommit
per statement), no business logic in this layer.

Tables:
  * routes_watchlist  — origin/dest pairs the detector polls (M1 fills it;
                        M0 seeds rows as sweeps discover destinations).
  * fare_observations — unified store for cached AND live observations;
                        baselines will query verified rows only.
  * deals             — candidate → verified → queued → approved/rejected
                        → published lifecycle (statuses per MVP-SPEC §2).
  * rejections        — one-tap rejection reasons (the tuning signal).
  * send_log          — every outbound send (audit + dedup base).
  * suppressions      — addresses we must never email again; honored
                        BEFORE every send (non-negotiable #7).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

DEALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS routes_watchlist (
    origin       TEXT NOT NULL,
    dest         TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    added_at     TEXT NOT NULL,
    seeded_since TEXT,
    obs_count    INTEGER NOT NULL DEFAULT 0,
    route_class  TEXT NOT NULL DEFAULT 'medium',  -- intra_eu|medium|long
    PRIMARY KEY (origin, dest)
);

CREATE TABLE IF NOT EXISTS fare_observations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    origin       TEXT NOT NULL,
    dest         TEXT NOT NULL,
    depart_date  TEXT NOT NULL,
    return_date  TEXT,               -- NULL = one-way
    price        INTEGER NOT NULL,
    currency     TEXT NOT NULL,
    source       TEXT NOT NULL,
    source_family TEXT NOT NULL,
    found_at     TEXT,               -- provider's cache timestamp when known
    observed_at  TEXT NOT NULL,      -- when WE stored it
    is_verified  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_fareobs_route
    ON fare_observations (origin, dest, observed_at);
CREATE INDEX IF NOT EXISTS idx_fareobs_verified
    ON fare_observations (is_verified, origin, dest);

CREATE TABLE IF NOT EXISTS deals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    origin            TEXT NOT NULL,
    dest              TEXT NOT NULL,
    depart_window     TEXT,
    return_window     TEXT,
    sample_dates      TEXT,           -- "YYYY-MM-DD..YYYY-MM-DD" example itinerary
    price             INTEGER NOT NULL,
    currency          TEXT NOT NULL DEFAULT 'EUR',
    baseline_median   INTEGER,        -- NULL until baselines mature (cold start)
    baseline_p10      INTEGER,
    pct_below         REAL,
    abs_saving        INTEGER,
    score             REAL,
    class             TEXT NOT NULL DEFAULT 'standard',  -- standard|mistake
    status            TEXT NOT NULL DEFAULT 'candidate',
    -- candidate|verified|queued|approved|rejected|expired|published
    draft_es          TEXT,
    draft_version     TEXT,
    verification_refs TEXT,           -- JSON: sources, live prices, notes
    confidence        TEXT,           -- JSON: lib/confidence.py as_dict()
    created_at        TEXT NOT NULL,
    approved_at       TEXT,
    published_at      TEXT,
    publish_targets   TEXT            -- JSON list, e.g. ["tg_private","email"]
);
CREATE INDEX IF NOT EXISTS idx_deals_route ON deals (origin, dest, created_at);
CREATE INDEX IF NOT EXISTS idx_deals_status ON deals (status, created_at);

CREATE TABLE IF NOT EXISTS rejections (
    deal_id  INTEGER NOT NULL,
    reason   TEXT NOT NULL,   -- too_common|bad_dates|ulcc_junk|thin_saving|other
    note     TEXT,
    ts       TEXT NOT NULL
);

-- Itineraries the LIVE market has already disproved. When verification
-- finds the cached price was fiction (live above cached + tolerance),
-- the cache will still be serving that same fiction on the next run —
-- three times a day, every day. Measured 2026-08-10..12: 19 of 30
-- verifications died this way, and six routes were re-nominated up to
-- 6x each, burning a daily-cap slot and a paid verification every time.
--
-- Scoped to (route + exact dates) and short-lived on purpose: a route
-- whose November fare was fiction may well have a real January one, and
-- a 24h window still re-checks daily in case the market moves.
CREATE TABLE IF NOT EXISTS disproved (
    origin      TEXT NOT NULL,
    dest        TEXT NOT NULL,
    depart_date TEXT NOT NULL,
    return_date TEXT NOT NULL DEFAULT '',   -- '' = one-way (NULLs break PKs)
    cached      INTEGER NOT NULL,
    live        INTEGER,
    until_ts    TEXT NOT NULL,
    ts          TEXT NOT NULL,
    PRIMARY KEY (origin, dest, depart_date, return_date)
);

CREATE INDEX IF NOT EXISTS idx_disproved_until ON disproved (until_ts);

CREATE TABLE IF NOT EXISTS send_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id    INTEGER,              -- NULL pre-membership (M0: Carlos)
    channel      TEXT NOT NULL,        -- email|tg_private|tg_public
    deal_id      INTEGER,
    ts           TEXT NOT NULL,
    provider_ref TEXT
);

CREATE TABLE IF NOT EXISTS suppressions (
    email  TEXT PRIMARY KEY,
    reason TEXT,
    ts     TEXT NOT NULL
);

-- Programmatic SEO route pages (M4b, D6): a page INDEXES only when its
-- route passes the detector's min_observations bar; intros are Claude-
-- generated once and refreshed quarterly (scripts/gen_seo_intros.py).
CREATE TABLE IF NOT EXISTS seo_pages (
    origin             TEXT NOT NULL,
    dest               TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'noindex',  -- published|noindex
    last_generated     TEXT,
    intro_es           TEXT,
    intro_generated_at TEXT,
    PRIMARY KEY (origin, dest)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# (table, column, ddl) — applied when the column is missing; the deals
# table already lives on prod Turso, so new columns arrive as ALTERs.
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("deals", "free_pick",
     "ALTER TABLE deals ADD COLUMN free_pick INTEGER NOT NULL DEFAULT 0"),
)


def ensure_deals_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DEALS_SCHEMA)
    for table, column, ddl in _MIGRATIONS:
        cols = {row[1] for row in conn.execute(
            f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(ddl)


@dataclass(frozen=True)
class Observation:
    origin: str
    dest: str
    depart_date: str
    return_date: str | None
    price: int
    currency: str
    source: str
    source_family: str
    found_at: str | None
    is_verified: bool = False


# Rows per INSERT batch over the Turso HTTP backend. The widened sweep
# (6 months x 2 sortings x 4 origins, breadth pass included) turned a
# ~700-row insert into a ~8000-row one, and a single executemany of that
# size reliably blew the 60s HTTP read timeout — the run died AFTER
# spending its whole free discovery budget. Chunking keeps each request
# comfortably inside the timeout; the table is append-only, so a partial
# insert is just fewer observations, never a corrupt state.
OBS_CHUNK = 400


def insert_observations(conn, obs: list[Observation]) -> int:
    now = _now_iso()
    payload = [
        (o.origin, o.dest, o.depart_date, o.return_date, o.price, o.currency,
         o.source, o.source_family, o.found_at, now, 1 if o.is_verified else 0)
        for o in obs
    ]
    if not payload:
        return 0
    for start in range(0, len(payload), OBS_CHUNK):
        conn.executemany(
            """
            INSERT INTO fare_observations
                (origin, dest, depart_date, return_date, price, currency,
                 source, source_family, found_at, observed_at, is_verified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload[start:start + OBS_CHUNK],
        )
    return len(payload)


def touch_watchlist(conn, *, origin: str, dest: str, route_class: str) -> None:
    """Upsert the route into the watchlist and bump its observation
    counter — sweeps grow the watchlist organically until M1 seeds it."""
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO routes_watchlist
            (origin, dest, active, added_at, seeded_since, obs_count, route_class)
        VALUES (?, ?, 1, ?, ?, 1, ?)
        ON CONFLICT(origin, dest) DO UPDATE SET
            obs_count = obs_count + 1
        """,
        (origin, dest, now, now, route_class),
    )


def insert_deal(conn, **cols) -> int:
    """Insert a deal row from keyword columns; returns the deal id."""
    cols.setdefault("created_at", _now_iso())
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO deals ({names}) VALUES ({marks})", tuple(cols.values()))
    rowid = cur.lastrowid
    if rowid is None:  # Turso HTTP cursor may not surface lastrowid
        rowid = conn.execute("SELECT MAX(id) FROM deals").fetchone()[0]
    return int(rowid)


def update_deal(conn, deal_id: int, **cols) -> None:
    if not cols:
        return
    sets = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(
        f"UPDATE deals SET {sets} WHERE id = ?", (*cols.values(), deal_id))


def record_rejection(conn, deal_id: int, *, reason: str,
                     note: str | None = None) -> None:
    conn.execute(
        "INSERT INTO rejections (deal_id, reason, note, ts) VALUES (?, ?, ?, ?)",
        (deal_id, reason, note, _now_iso()),
    )


def record_disproved(conn, *, origin: str, dest: str, depart_date: str,
                     return_date: str | None, cached: int, live: int | None,
                     hours: int) -> None:
    """Mark one itinerary as disproved by the live market for `hours`.

    Upsert, not insert: a route re-checked after its window expires and
    disproved again must extend, not collide on the primary key."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    until = (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        INSERT INTO disproved
            (origin, dest, depart_date, return_date, cached, live, until_ts, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (origin, dest, depart_date, return_date) DO UPDATE SET
            cached = excluded.cached, live = excluded.live,
            until_ts = excluded.until_ts, ts = excluded.ts
        """,
        (origin.upper(), dest.upper(), depart_date, return_date or "",
         int(cached), int(live) if live is not None else None,
         until, now.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )


def active_disproved(conn) -> set[tuple[str, str, str, str]]:
    """The itineraries still inside their disproved window. Loaded once
    per gate pass — the gate compares thousands of observations and must
    not issue a query per row."""
    rows = conn.execute(
        "SELECT origin, dest, depart_date, return_date FROM disproved "
        "WHERE until_ts > ?",
        (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),),
    ).fetchall()
    return {(r["origin"], r["dest"], r["depart_date"], r["return_date"] or "")
            for r in rows}


def record_send(conn, *, channel: str, deal_id: int | None,
                provider_ref: str | None, member_id: int | None = None) -> None:
    conn.execute(
        """
        INSERT INTO send_log (member_id, channel, deal_id, ts, provider_ref)
        VALUES (?, ?, ?, ?, ?)
        """,
        (member_id, channel, deal_id, _now_iso(), provider_ref),
    )


def is_suppressed(conn, email: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM suppressions WHERE email = ?", (email,)).fetchone()
    return row is not None


def deals_created_today(conn, *, today_prefix: str) -> int:
    """Candidate-cap input: deals created on `today_prefix` (YYYY-MM-DD)."""
    return conn.execute(
        "SELECT COUNT(*) FROM deals WHERE created_at LIKE ?",
        (today_prefix + "%",),
    ).fetchone()[0]


def last_deal_for_route(conn, *, origin: str, dest: str):
    """Most recent deal that actually became (or is becoming) an ALERT —
    the cooldown/dedup reference. Dead rows (rejected, expired, and
    candidates/verifieds stranded by a degraded run) must not mute the
    route: they never reached anyone."""
    return conn.execute(
        """
        SELECT * FROM deals
        WHERE origin = ? AND dest = ?
          AND status IN ('queued', 'approved', 'published')
        ORDER BY created_at DESC LIMIT 1
        """,
        (origin, dest),
    ).fetchone()
