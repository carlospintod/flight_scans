import { NextResponse, type NextRequest } from "next/server";
import { isOpsBreakGlass } from "@/lib/auth";
import { db } from "@/lib/db";
import { getCredentials, MANAGED_KEYS } from "@/lib/queries";
import { requireUser } from "@/lib/users";

/** Owner-only API-key management. GET returns MASKED status only (full
 *  values never leave the DB). POST sets or clears one key. These are
 *  low-value free-tier flight keys; the scanner loads them into env at
 *  startup (DB overrides env). */

async function authorized(): Promise<number | null> {
  const user = await requireUser("owner");
  if (user) return user.userId;
  if (await isOpsBreakGlass()) return 1;
  return null;
}

export async function GET() {
  if ((await authorized()) === null) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  return NextResponse.json({ credentials: await getCredentials() });
}

export async function POST(req: NextRequest) {
  const adminId = await authorized();
  if (adminId === null) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  let body: { envVar?: string; value?: string } = {};
  try {
    body = await req.json();
  } catch {
    /* validated below */
  }
  const envVar = String(body.envVar ?? "");
  const value = String(body.value ?? "").trim();
  if (!MANAGED_KEYS.some((k) => k.envVar === envVar)) {
    return NextResponse.json({ error: "unknown key" }, { status: 400 });
  }
  // The Python scanner owns the schema, but the owner may set a key
  // before the first scan ever runs — create the table if missing.
  await db().execute(
    `CREATE TABLE IF NOT EXISTS source_credentials (
       env_var TEXT PRIMARY KEY, value TEXT NOT NULL,
       updated_at TEXT NOT NULL, updated_by INTEGER)`,
  );
  const now = new Date().toISOString();
  if (!value) {
    // Empty value clears the key (falls back to env on the next scan).
    await db().execute({
      sql: "DELETE FROM source_credentials WHERE env_var = ?",
      args: [envVar],
    });
    return NextResponse.json({ ok: true, cleared: true });
  }
  await db().execute({
    sql: `INSERT INTO source_credentials (env_var, value, updated_at, updated_by)
          VALUES (?, ?, ?, ?)
          ON CONFLICT(env_var) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at,
            updated_by = excluded.updated_by`,
    args: [envVar, value, now, adminId],
  });
  return NextResponse.json({ ok: true });
}
