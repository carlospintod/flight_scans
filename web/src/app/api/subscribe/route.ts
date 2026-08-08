// Free-list signup (M3/M4a): the landing form writes here. The free
// tier is the marketing department (growth guideline #2) — the list
// lives in Turso, never in an ESP's contacts product (D4).

import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { db } from "@/lib/db";
import { newMemberToken } from "@/lib/member-auth";

const WINDOW_MS = 15 * 60 * 1000;
const MAX_ATTEMPTS = 10;

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

async function ensureTables() {
  await db().execute(
    `CREATE TABLE IF NOT EXISTS subscribers (
       email TEXT PRIMARY KEY,
       status TEXT NOT NULL DEFAULT 'active',
       source TEXT,
       unsub_token TEXT,
       created_at TEXT NOT NULL)`,
  );
  await db().execute(
    `CREATE TABLE IF NOT EXISTS suppressions (
       email TEXT PRIMARY KEY, reason TEXT, ts TEXT NOT NULL)`,
  );
}

export async function POST(req: NextRequest) {
  const ip =
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
  // Scoped bucket: never shares quota with member/ops logins.
  await db().execute(
    `CREATE TABLE IF NOT EXISTS login_attempts_subscribe (
       ip TEXT NOT NULL, attempted_at INTEGER NOT NULL)`,
  );
  const rs = await db().execute({
    sql: "SELECT COUNT(*) AS n FROM login_attempts_subscribe WHERE ip = ? AND attempted_at >= ?",
    args: [ip, Date.now() - WINDOW_MS],
  });
  if (Number(rs.rows[0]?.["n"] ?? 0) >= MAX_ATTEMPTS) {
    return NextResponse.json({ error: "demasiados intentos" }, { status: 429 });
  }
  await db().execute({
    sql: "INSERT INTO login_attempts_subscribe (ip, attempted_at) VALUES (?, ?)",
    args: [ip, Date.now()],
  });
  await db().execute({
    sql: "DELETE FROM login_attempts_subscribe WHERE attempted_at < ?",
    args: [Date.now() - 24 * 3600 * 1000],
  });

  let email = "";
  try {
    email = z
      .object({ email: z.string().email() })
      .parse(await req.json())
      .email.toLowerCase();
  } catch {
    return NextResponse.json({ error: "email inválido" }, { status: 400 });
  }
  await ensureTables();
  await db().execute({
    sql: `INSERT INTO subscribers (email, status, source, unsub_token, created_at)
          VALUES (?, 'active', 'landing', ?, ?)
          ON CONFLICT(email) DO UPDATE SET status = 'active'`,
    args: [email, newMemberToken(), nowIso()],
  });
  // An existing suppression is deliberately NOT cleared here: this
  // endpoint is unauthenticated, so clearing it would let any third
  // party undo someone's one-click unsubscribe by re-posting their
  // address. The suppression check before every bulk send keeps the
  // address dark; re-enabling requires the address owner (members via
  // /cuenta, free-list via hola@vuelazo.es).
  return NextResponse.json({ ok: true });
}
