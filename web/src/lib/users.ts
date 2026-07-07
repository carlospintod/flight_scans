import { db } from "./db";
import {
  getUserSession,
  hashToken,
  newLinkToken,
  type UserSession,
} from "./auth";

/** User/token data access. Link-based auth (product plan M3): tokens are
 *  minted by the owner, stored SHA-256-hashed, single-use via
 *  single-statement CAS consume — safe on the autocommit Turso backend. */

export interface UserRow {
  userId: number;
  email: string | null;
  displayName: string | null;
  role: string;
  sessionVersion: number;
  createdAt: string;
}

const INVITE_EXPIRY_S = 7 * 86_400;   // first-time links: a week to click
const LOGIN_EXPIRY_S = 30 * 60;       // re-login links: 30 minutes

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

export async function getUser(userId: number): Promise<UserRow | null> {
  const rs = await db().execute({
    sql: "SELECT * FROM users WHERE user_id = ?",
    args: [userId],
  });
  const r = rs.rows[0];
  if (!r) return null;
  return {
    userId: Number(r["user_id"]),
    email: r["email"] ? String(r["email"]) : null,
    displayName: r["display_name"] ? String(r["display_name"]) : null,
    role: String(r["role"]),
    sessionVersion: Number(r["session_version"]),
    createdAt: String(r["created_at"]),
  };
}

/** Mutating-route guard: valid v2 cookie AND live session_version (one
 *  DB read — bumping users.session_version revokes everywhere). */
export async function requireUser(
  minRole?: "owner",
): Promise<(UserSession & { role: string }) | null> {
  const session = await getUserSession();
  if (!session) return null;
  const user = await getUser(session.userId);
  if (!user || user.sessionVersion !== session.sv) return null;
  if (minRole === "owner" && user.role !== "owner") return null;
  return { ...session, role: user.role };
}

export async function listUsers(): Promise<
  (UserRow & { activeSearches: number })[]
> {
  const rs = await db().execute(
    `SELECT u.*, (SELECT COUNT(*) FROM searches s
                  WHERE s.user_id = u.user_id AND s.status = 'active')
                 AS active_searches
     FROM users u ORDER BY u.user_id`,
  );
  return rs.rows.map((r) => ({
    userId: Number(r["user_id"]),
    email: r["email"] ? String(r["email"]) : null,
    displayName: r["display_name"] ? String(r["display_name"]) : null,
    role: String(r["role"]),
    sessionVersion: Number(r["session_version"]),
    createdAt: String(r["created_at"]),
    activeSearches: Number(r["active_searches"]),
  }));
}

/** Create a user (invite) — or return the existing one by email. */
export async function upsertInvitee(
  email: string, displayName: string, invitedBy: number,
): Promise<number> {
  await db().execute({
    sql: `INSERT OR IGNORE INTO users
            (email, display_name, role, session_version, invited_by, created_at)
          VALUES (?, ?, 'user', 1, ?, ?)`,
    args: [email, displayName, invitedBy, nowIso()],
  });
  const rs = await db().execute({
    sql: "SELECT user_id FROM users WHERE email = ?",
    args: [email],
  });
  return Number(rs.rows[0]["user_id"]);
}

/** Mint a signed one-time link token for a user. The RAW token goes in
 *  the URL FRAGMENT (never server logs); only its hash is stored. */
export async function mintLinkToken(
  userId: number, purpose: "invite" | "login",
): Promise<string> {
  const token = newLinkToken();
  const expiry = purpose === "invite" ? INVITE_EXPIRY_S : LOGIN_EXPIRY_S;
  const expiresAt = new Date(Date.now() + expiry * 1000)
    .toISOString().replace(/\.\d{3}Z$/, "Z");
  await db().execute({
    sql: `INSERT INTO login_tokens
            (token_hash, user_id, purpose, expires_at, created_at)
          VALUES (?, ?, ?, ?, ?)`,
    args: [await hashToken(token), userId, purpose, expiresAt, nowIso()],
  });
  return token;
}

/** Single-statement CAS consume: exactly one click wins, even racing. */
export async function consumeLinkToken(
  token: string,
): Promise<{ userId: number } | null> {
  const hash = await hashToken(token);
  const now = nowIso();
  const rs = await db().execute({
    sql: `UPDATE login_tokens SET consumed_at = ?
          WHERE token_hash = ? AND consumed_at IS NULL AND expires_at > ?`,
    args: [now, hash, now],
  });
  if (rs.rowsAffected !== 1) return null;
  const row = await db().execute({
    sql: "SELECT user_id FROM login_tokens WHERE token_hash = ?",
    args: [hash],
  });
  return { userId: Number(row.rows[0]["user_id"]) };
}

/** GDPR deletion: ordered, re-runnable, fat-tables-first. spend_events
 *  are anonymized (accounting rows stay, the search link dies). */
export async function deleteUserCascade(userId: number): Promise<void> {
  const client = db();
  const rs = await client.execute({
    sql: "SELECT search_id FROM searches WHERE user_id = ?",
    args: [userId],
  });
  for (const r of rs.rows) {
    const sid = String(r["search_id"]);
    for (const table of ["calendar_snapshots", "point_queries", "alerts",
                         "scan_runs", "departure_curves"]) {
      await client.execute({
        sql: `DELETE FROM ${table} WHERE route_id = ?`, args: [sid],
      });
    }
    await client.execute({
      sql: "UPDATE spend_events SET search_id = NULL WHERE search_id = ?",
      args: [sid],
    });
    await client.execute({
      sql: "DELETE FROM run_reservations WHERE search_id = ?", args: [sid],
    });
    await client.execute({
      sql: "DELETE FROM routes WHERE route_id = ?", args: [sid],
    });
    await client.execute({
      sql: "DELETE FROM searches WHERE search_id = ?", args: [sid],
    });
  }
  await client.execute({
    sql: "DELETE FROM login_tokens WHERE user_id = ?", args: [userId],
  });
  await client.execute({
    sql: "DELETE FROM users WHERE user_id = ?", args: [userId],
  });
}
