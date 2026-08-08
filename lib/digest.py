"""Weekly digest + free-tier plumbing (M3, D4).

The digest self-assembles from the week's APPROVED deals — the drafts
are already Claude-written and human-approved, so assembly is pure
composition (no new API spend). One Sunday review, then send to the
free list (subscribers minus suppressions).

Free/paid line (D4): the free channel + digest carry the 1–3 genuinely
excellent deals/week the approver flagged (free_pick), published T+24h
after members. Free quality is the trust engine — never junk.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

DIGEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS digests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL UNIQUE,   -- Monday YYYY-MM-DD
    draft_es   TEXT NOT NULL,
    n_deals    INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'draft',  -- draft|approved|sent
    created_at TEXT NOT NULL,
    sent_at    TEXT
);

-- The free list (landing signups, M4a). The paid list lives in members.
-- unsub_token: per-subscriber one-click unsubscribe credential (the web
-- signup mints it; digest sends embed it in List-Unsubscribe).
CREATE TABLE IF NOT EXISTS subscribers (
    email       TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'active',  -- active|unsub
    source      TEXT,                            -- landing|import|manual
    unsub_token TEXT,
    created_at  TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def ensure_digest_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DIGEST_SCHEMA)


def week_start(now: datetime | None = None) -> str:
    """Monday of the current week (the digest keys on it)."""
    now = now or datetime.now(timezone.utc)
    monday = now.date() - timedelta(days=now.weekday())
    return monday.isoformat()


def week_deals(conn, *, week_start_iso: str) -> list[sqlite3.Row]:
    """The week's approved/published deals, best score first."""
    week_end = (datetime.fromisoformat(week_start_iso)
                + timedelta(days=7)).date().isoformat()
    return list(conn.execute(
        """
        SELECT * FROM deals
        WHERE status IN ('approved', 'published')
          AND approved_at >= ? AND approved_at < ?
        ORDER BY score DESC
        """,
        (week_start_iso, week_end)).fetchall())


def assemble_digest(conn, *, week_start_iso: str | None = None,
                    now: datetime | None = None) -> int | None:
    """Compose the week's digest from approved drafts; one row per week
    (UNIQUE week_start). While the digest is still a DRAFT, re-assembly
    REFRESHES its content — deals approved after the Sunday-morning cron
    but before the send still make it in. Sent/approved digests are
    immutable. Returns the digest id, or None when the week produced
    nothing (no digest beats a junk digest — D4)."""
    ws = week_start_iso or week_start(now)
    existing = conn.execute(
        "SELECT id, status FROM digests WHERE week_start = ?",
        (ws,)).fetchone()
    if existing and existing["status"] != "draft":
        return existing["id"]
    deals = week_deals(conn, week_start_iso=ws)
    if not deals:
        return existing["id"] if existing else None
    parts = [
        "VUELAZO — el resumen de la semana",
        "",
        f"Los {len(deals)} mejores chollos que verificamos esta semana "
        "(los miembros los recibieron al instante):",
        "",
    ]
    for i, d in enumerate(deals, 1):
        parts.append(f"— {i} · {d['origin']} → {d['dest']} · "
                     f"{d['price']} {d['currency']} —")
        parts.append(d["draft_es"] or "(sin texto)")
        parts.append("")
    parts.append("¿Quieres los chollos al instante y solo de tus "
                 "aeropuertos? El pase anual: vuelazo.es/unete")
    text = "\n".join(parts)
    if existing:  # draft refresh
        conn.execute(
            "UPDATE digests SET draft_es = ?, n_deals = ? "
            "WHERE id = ? AND status = 'draft'",
            (text, len(deals), existing["id"]))
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO digests (week_start, draft_es, n_deals, status, "
        "created_at) VALUES (?, ?, ?, 'draft', ?)",
        (ws, text, len(deals), _now_iso()))
    rowid = cur.lastrowid or conn.execute(
        "SELECT MAX(id) FROM digests").fetchone()[0]
    return int(rowid)


def approve_digest(conn, digest_id: int) -> bool:
    cur = conn.execute(
        "UPDATE digests SET status = 'approved' "
        "WHERE id = ? AND status = 'draft'", (digest_id,))
    return (cur.rowcount or 0) == 1


def mark_digest_sent(conn, digest_id: int) -> None:
    conn.execute(
        "UPDATE digests SET status = 'sent', sent_at = ? WHERE id = ?",
        (_now_iso(), digest_id))


def active_subscribers(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT s.email FROM subscribers s
        WHERE s.status = 'active'
          AND NOT EXISTS (SELECT 1 FROM suppressions x
                          WHERE x.email = s.email)
        ORDER BY s.email
        """).fetchall()
    return [r["email"] for r in rows]


# -- Free channel, T+24h (M3 automation) ------------------------------------

def free_picks_due(conn, *, now: datetime | None = None) -> list[sqlite3.Row]:
    """Published free-pick deals whose 24h member exclusivity has passed
    and which haven't hit the public channel yet (send_log is the
    dedup)."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return list(conn.execute(
        """
        SELECT * FROM deals
        WHERE status = 'published' AND free_pick = 1
          AND published_at <= ?
          AND NOT EXISTS (SELECT 1 FROM send_log sl
                          WHERE sl.deal_id = deals.id
                            AND sl.channel = 'tg_public')
        ORDER BY published_at
        """,
        (cutoff,)).fetchall())
