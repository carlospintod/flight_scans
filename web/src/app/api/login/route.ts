import { NextResponse, type NextRequest } from "next/server";
import { db } from "@/lib/db";
import {
  SESSION_COOKIE,
  checkPassword,
  makeSessionValue,
  sessionCookieOptions,
} from "@/lib/auth";

const WINDOW_MS = 15 * 60 * 1000;
const MAX_ATTEMPTS = 10;

/** Serverless has no useful in-memory state, so the login rate limit
 *  lives in Turso (the plan's design). Both failures and successes are
 *  recorded; stale rows are pruned opportunistically. */
export async function POST(req: NextRequest) {
  const ip =
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
  const client = db();
  await client.execute(
    `CREATE TABLE IF NOT EXISTS login_attempts (
       ip TEXT NOT NULL, attempted_at INTEGER NOT NULL)`,
  );
  const since = Date.now() - WINDOW_MS;
  const rs = await client.execute({
    sql: "SELECT COUNT(*) AS n FROM login_attempts WHERE ip = ? AND attempted_at >= ?",
    args: [ip, since],
  });
  if (Number(rs.rows[0]?.["n"] ?? 0) >= MAX_ATTEMPTS) {
    return NextResponse.json(
      { error: "too many attempts — try again in 15 minutes" },
      { status: 429 },
    );
  }
  await client.execute({
    sql: "INSERT INTO login_attempts (ip, attempted_at) VALUES (?, ?)",
    args: [ip, Date.now()],
  });
  // Opportunistic prune (cheap, keeps the table tiny).
  await client.execute({
    sql: "DELETE FROM login_attempts WHERE attempted_at < ?",
    args: [Date.now() - 24 * 3600 * 1000],
  });

  let password = "";
  try {
    const body = await req.json();
    password = String(body?.password ?? "");
  } catch {
    /* fall through to failure */
  }
  if (!password || !(await checkPassword(password))) {
    return NextResponse.json({ error: "wrong password" }, { status: 401 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, await makeSessionValue(),
    sessionCookieOptions());
  return res;
}
