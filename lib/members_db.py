"""Vuelazo membership storage (MVP-SPEC §2, D5) — money & members are
sacred paths: every entitlement transition is logged (member_events),
Stripe webhooks are idempotent (stripe_events PK), suppressions are
honored before every send (deals_db.is_suppressed).

Same conventions as deals_db: no FKs, single-statement CAS mutations,
web mirrors these tables with raw SQL.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

MEMBERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    email              TEXT NOT NULL UNIQUE,
    status             TEXT NOT NULL DEFAULT 'active',  -- active|lapsed|refunded
    member_until       TEXT NOT NULL,
    plan               TEXT NOT NULL,                   -- founding|list
    price_paid         INTEGER,                         -- cents, IVA incl.
    stripe_customer_id TEXT,
    stripe_payment_ref TEXT UNIQUE,                     -- checkout session / PI id
    telegram_user_id   INTEGER,
    airports           TEXT NOT NULL DEFAULT '["MAD","BCN","VLC","ALC"]',
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_members_status ON members (status, member_until);

-- Entitlement audit trail (non-negotiable #7).
CREATE TABLE IF NOT EXISTS member_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    event     TEXT NOT NULL,   -- created|renewed|reminded_t30|reminded_t7|
                               -- lapsed|refunded|tg_bound|tg_removed|airports_set
    detail    TEXT,
    ts        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_member_events ON member_events (member_id, ts);

-- Stripe webhook idempotency: INSERT OR IGNORE on the event id; only
-- the inserting request processes the event.
CREATE TABLE IF NOT EXISTS stripe_events (
    event_id     TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

-- One-time tokens for members (parallel to login_tokens, which belongs
-- to the tracker's users). SHA-256 hash stored, single-statement CAS
-- consume. purpose: login | tg_bind.
CREATE TABLE IF NOT EXISTS member_tokens (
    token_hash  TEXT PRIMARY KEY,
    member_id   INTEGER NOT NULL,
    purpose     TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    consumed_at TEXT,
    created_at  TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def ensure_members_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(MEMBERS_SCHEMA)


def mint_token(conn, member_id: int, *, purpose: str,
               days: int = 730) -> str:
    """One-time member token (login | tg_bind | unsub). Returns the RAW
    token; only the SHA-256 hash is stored (the web mirrors this)."""
    import hashlib
    import secrets
    token = secrets.token_hex(24)
    conn.execute(
        "INSERT INTO member_tokens (token_hash, member_id, purpose, "
        "expires_at, consumed_at, created_at) VALUES (?, ?, ?, ?, NULL, ?)",
        (hashlib.sha256(token.encode()).hexdigest(), member_id, purpose,
         (datetime.now(timezone.utc) + timedelta(days=days)).strftime(
             "%Y-%m-%dT%H:%M:%SZ"),
         _now_iso()))
    return token


def log_event(conn, member_id: int, event: str,
              detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO member_events (member_id, event, detail, ts) "
        "VALUES (?, ?, ?, ?)",
        (member_id, event, detail, _now_iso()))


def active_members_for_origin(conn, origin: str) -> list[sqlite3.Row]:
    """The per-airport alert audience (D4 personalization layer):
    active, unexpired, airports JSON containing the origin."""
    now = _now_iso()
    rows = conn.execute(
        """
        SELECT * FROM members
        WHERE status = 'active' AND member_until > ?
          AND airports LIKE ?
        """,
        (now, f'%"{origin.upper()}"%'),
    ).fetchall()
    return list(rows)


def count_active_members(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM members WHERE status = 'active' "
        "AND member_until > ?", (_now_iso(),)).fetchone()[0]


def members_to_lapse(conn) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM members WHERE status = 'active' AND member_until <= ?",
        (_now_iso(),)).fetchall())


def lapse_member(conn, member_id: int) -> bool:
    """CAS: active -> lapsed. True when this call made the transition."""
    cur = conn.execute(
        "UPDATE members SET status = 'lapsed' "
        "WHERE id = ? AND status = 'active'", (member_id,))
    if (cur.rowcount or 0) == 1:
        log_event(conn, member_id, "lapsed")
        return True
    return False


def reminder_due(conn, *, days_ahead: int, event: str,
                 min_days_ahead: int = 0) -> list[sqlite3.Row]:
    """Active members whose pass expires within (min_days_ahead,
    days_ahead] days and who have NOT yet received this reminder
    (member_events is the dedup).

    min_days_ahead keeps a late-firing T-30 honest: after an outage a
    member at T-5 must get the T-7 mail only, never a 'caduca en 30
    días' claim (pass min_days_ahead=7 for the T-30 batch)."""
    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(days=days_ahead)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    floor = (now + timedelta(days=min_days_ahead)).replace(
        microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Dedup: the same reminder within the last 45 days is suppressed —
    # by the time 45 days pass, the member has either renewed (pushed
    # member_until out of the horizon) or lapsed (left 'active').
    # Cutoff computed HERE in the canonical ...T...Z format — mixing
    # SQLite's datetime('now') space-separated format into the string
    # comparison only worked by the accident of 'T' > ' '.
    dedup_cutoff = (now - timedelta(days=45)).replace(
        microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    return list(conn.execute(
        """
        SELECT m.* FROM members m
        WHERE m.status = 'active'
          AND m.member_until > ?
          AND m.member_until <= ?
          AND NOT EXISTS (
            SELECT 1 FROM member_events e
            WHERE e.member_id = m.id AND e.event = ?
              AND e.ts >= ?)
        """,
        (floor, horizon, event, dedup_cutoff)).fetchall())


def members_needing_removal(conn) -> list[sqlite3.Row]:
    """Everyone whose channel access must end but whose telegram id is
    still bound — lapsed AND refunded alike. Re-selected every run until
    the removal SUCCEEDS (telegram_user_id is NULLed only then), so a
    failed/skipped removal is retried instead of fire-and-forgotten."""
    return list(conn.execute(
        "SELECT * FROM members WHERE status IN ('lapsed', 'refunded') "
        "AND telegram_user_id IS NOT NULL").fetchall())
