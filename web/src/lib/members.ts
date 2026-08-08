// Member data access (M2). Tables are owned by lib/members_db.py; SQL
// here mirrors it 1:1. Money & members are sacred paths: transitions go
// through logMemberEvent, mutations are single-statement CAS.

import { cookies } from "next/headers";
import { db } from "@/lib/db";
import {
  MEMBER_COOKIE,
  newMemberToken,
  sha256Hex,
  verifyMemberSessionValue,
} from "@/lib/member-auth";

export type MemberRow = {
  id: number;
  email: string;
  status: string; // active | lapsed | refunded
  memberUntil: string;
  plan: string; // founding | list
  pricePaid: number | null;
  stripeCustomerId: string | null;
  stripePaymentRef: string | null;
  telegramUserId: number | null;
  airports: string[];
  createdAt: string;
};

export function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function rowToMember(r: Record<string, unknown>): MemberRow {
  let airports: string[] = [];
  try {
    const parsed = JSON.parse(String(r["airports"] ?? "[]"));
    if (Array.isArray(parsed)) airports = parsed.map(String);
  } catch {
    /* default [] */
  }
  return {
    id: Number(r["id"]),
    email: String(r["email"]),
    status: String(r["status"]),
    memberUntil: String(r["member_until"]),
    plan: String(r["plan"]),
    pricePaid: r["price_paid"] == null ? null : Number(r["price_paid"]),
    stripeCustomerId: r["stripe_customer_id"] ? String(r["stripe_customer_id"]) : null,
    stripePaymentRef: r["stripe_payment_ref"] ? String(r["stripe_payment_ref"]) : null,
    telegramUserId: r["telegram_user_id"] == null ? null : Number(r["telegram_user_id"]),
    airports,
    createdAt: String(r["created_at"]),
  };
}

// The Python side (lib/members_db.py) owns this schema; the webhook may
// fire before the first cron run, so create defensively (the sanctioned
// pattern, see api/admin/credentials).
export async function ensureMemberTables(): Promise<void> {
  await db().execute(
    `CREATE TABLE IF NOT EXISTS members (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       email TEXT NOT NULL UNIQUE,
       status TEXT NOT NULL DEFAULT 'active',
       member_until TEXT NOT NULL,
       plan TEXT NOT NULL,
       price_paid INTEGER,
       stripe_customer_id TEXT,
       stripe_payment_ref TEXT UNIQUE,
       telegram_user_id INTEGER,
       airports TEXT NOT NULL DEFAULT '["MAD","BCN","VLC","ALC"]',
       created_at TEXT NOT NULL)`,
  );
  await db().execute(
    `CREATE TABLE IF NOT EXISTS member_events (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       member_id INTEGER NOT NULL, event TEXT NOT NULL,
       detail TEXT, ts TEXT NOT NULL)`,
  );
  await db().execute(
    `CREATE TABLE IF NOT EXISTS stripe_events (
       event_id TEXT PRIMARY KEY, type TEXT NOT NULL,
       processed_at TEXT NOT NULL)`,
  );
  await db().execute(
    `CREATE TABLE IF NOT EXISTS member_tokens (
       token_hash TEXT PRIMARY KEY, member_id INTEGER NOT NULL,
       purpose TEXT NOT NULL, expires_at TEXT NOT NULL,
       consumed_at TEXT, created_at TEXT NOT NULL)`,
  );
  await db().execute(
    `CREATE TABLE IF NOT EXISTS suppressions (
       email TEXT PRIMARY KEY, reason TEXT, ts TEXT NOT NULL)`,
  );
}

/** Non-negotiable #7: the suppression list is honored before every
 *  send — including web-tier transactional mail. */
export async function isSuppressed(email: string): Promise<boolean> {
  try {
    const rs = await db().execute({
      sql: "SELECT 1 FROM suppressions WHERE email = ?",
      args: [email.toLowerCase()],
    });
    return rs.rows.length > 0;
  } catch {
    return false; // fresh DB without the table: nothing suppressed yet
  }
}

export async function logMemberEvent(
  memberId: number,
  event: string,
  detail?: string,
): Promise<void> {
  await db().execute({
    sql: "INSERT INTO member_events (member_id, event, detail, ts) VALUES (?, ?, ?, ?)",
    args: [memberId, event, detail ?? null, nowIso()],
  });
}

export async function getMember(id: number): Promise<MemberRow | null> {
  const rs = await db().execute({
    sql: "SELECT * FROM members WHERE id = ?",
    args: [id],
  });
  return rs.rows[0] ? rowToMember(rs.rows[0] as Record<string, unknown>) : null;
}

export async function getMemberByEmail(email: string): Promise<MemberRow | null> {
  const rs = await db().execute({
    sql: "SELECT * FROM members WHERE email = ?",
    args: [email.toLowerCase()],
  });
  return rs.rows[0] ? rowToMember(rs.rows[0] as Record<string, unknown>) : null;
}

/** The logged-in member from the vz_member cookie, or null. */
export async function getSessionMember(): Promise<MemberRow | null> {
  const jar = await cookies();
  const raw = jar.get(MEMBER_COOKIE)?.value;
  if (!raw) return null;
  const session = await verifyMemberSessionValue(raw);
  if (!session) return null;
  return getMember(session.memberId);
}

/** Create-or-renew from a completed Stripe Checkout (idempotency is the
 *  caller's job via stripe_events). Returns {member, created}. */
export async function upsertMemberFromCheckout(opts: {
  email: string;
  plan: string;
  pricePaid: number;
  stripeCustomerId: string | null;
  stripePaymentRef: string;
}): Promise<{ member: MemberRow; created: boolean }> {
  const email = opts.email.toLowerCase();
  const existing = await getMemberByEmail(email);
  const now = new Date();
  if (existing) {
    // Renewal: extend from the current expiry when still in the future
    // (on-time renewal keeps the remaining days), else from now.
    const base = new Date(existing.memberUntil) > now
      ? new Date(existing.memberUntil)
      : now;
    const until = new Date(base.getTime() + 365 * 864e5)
      .toISOString()
      .replace(/\.\d{3}Z$/, "Z");
    await db().execute({
      sql: `UPDATE members SET status = 'active', member_until = ?,
              plan = ?, price_paid = ?, stripe_customer_id = ?,
              stripe_payment_ref = ?
            WHERE id = ?`,
      args: [until, opts.plan, opts.pricePaid, opts.stripeCustomerId,
             opts.stripePaymentRef, existing.id],
    });
    await logMemberEvent(existing.id, "renewed", `until ${until}`);
    return { member: (await getMember(existing.id))!, created: false };
  }
  const until = new Date(now.getTime() + 365 * 864e5)
    .toISOString()
    .replace(/\.\d{3}Z$/, "Z");
  await db().execute({
    sql: `INSERT INTO members (email, status, member_until, plan, price_paid,
            stripe_customer_id, stripe_payment_ref, created_at)
          VALUES (?, 'active', ?, ?, ?, ?, ?, ?)`,
    args: [email, until, opts.plan, opts.pricePaid, opts.stripeCustomerId,
           opts.stripePaymentRef, nowIso()],
  });
  const member = (await getMemberByEmail(email))!;
  await logMemberEvent(member.id, "created", `plan ${opts.plan}`);
  return { member, created: true };
}

/** Mint a one-time member token (login | tg_bind | unsub); stores only
 *  the SHA-256 hash. Mirrors lib/members_db.mint_token. */
export async function mintMemberToken(
  memberId: number,
  purpose: "login" | "tg_bind" | "unsub",
  days = 7,
): Promise<string> {
  const token = newMemberToken();
  const expires = new Date(Date.now() + days * 864e5)
    .toISOString()
    .replace(/\.\d{3}Z$/, "Z");
  await db().execute({
    sql: `INSERT INTO member_tokens (token_hash, member_id, purpose,
            expires_at, consumed_at, created_at)
          VALUES (?, ?, ?, ?, NULL, ?)`,
    args: [await sha256Hex(token), memberId, purpose, expires, nowIso()],
  });
  return token;
}

/** Read a token WITHOUT consuming it — for flows that must validate
 *  preconditions (member status) before burning a single-use token. */
export async function peekMemberToken(
  token: string,
  purpose: string,
): Promise<number | null> {
  const hash = await sha256Hex(token);
  const rs = await db().execute({
    sql: `SELECT member_id FROM member_tokens
          WHERE token_hash = ? AND purpose = ? AND consumed_at IS NULL
            AND expires_at > ?`,
    args: [hash, purpose, nowIso()],
  });
  return rs.rows[0] ? Number(rs.rows[0]["member_id"]) : null;
}

/** Single-statement CAS consume; returns the member id or null. */
export async function consumeMemberToken(
  token: string,
  purpose: string,
): Promise<number | null> {
  const hash = await sha256Hex(token);
  const rs = await db().execute({
    sql: `UPDATE member_tokens SET consumed_at = ?
          WHERE token_hash = ? AND purpose = ? AND consumed_at IS NULL
            AND expires_at > ?`,
    args: [nowIso(), hash, purpose, nowIso()],
  });
  if (rs.rowsAffected !== 1) return null;
  const row = await db().execute({
    sql: "SELECT member_id FROM member_tokens WHERE token_hash = ?",
    args: [hash],
  });
  return row.rows[0] ? Number(row.rows[0]["member_id"]) : null;
}
