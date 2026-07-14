import { NextResponse, type NextRequest } from "next/server";
import { isOpsBreakGlass } from "@/lib/auth";
import { routeConfigSchema } from "@/lib/config-schema";
import { db } from "@/lib/db";
import { requireUser } from "@/lib/users";

const ROUTE_ID = "spain-nairobi";

export async function GET() {
  const rs = await db().execute({
    sql: "SELECT config_json, updated_at FROM routes WHERE route_id = ?",
    args: [ROUTE_ID],
  });
  if (rs.rows.length === 0) {
    return NextResponse.json({ error: "route not found" }, { status: 404 });
  }
  return NextResponse.json({
    config: JSON.parse(String(rs.rows[0]["config_json"])),
    updatedAt: String(rs.rows[0]["updated_at"]),
  });
}

/** Writes the ONE cell the web app owns: routes.config_json. Same UPSERT
 *  statement as lib/db.upsert_route, so the Python scan pipeline picks
 *  the edit up on its next run (route_store precedence: DB wins). */
export async function PUT(req: NextRequest) {
  // Owner via the v2 mint-link session, OR the legacy APP_PASSWORD
  // break-glass. (Was isAuthed() only — which rejects v2 cookies.)
  if (!(await requireUser("owner")) && !(await isOpsBreakGlass())) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON" }, { status: 400 });
  }
  const parsed = routeConfigSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: parsed.error.issues.map(
          (i) => `${i.path.join(".")}: ${i.message}`).join("; ") },
      { status: 400 },
    );
  }
  if (parsed.data.route.name !== ROUTE_ID) {
    return NextResponse.json(
      { error: `route name must be ${ROUTE_ID}` },
      { status: 400 },
    );
  }
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  // Stable key order (sort_keys=true) matches Python's json.dumps so the
  // self-heal comparison in route_store stays a no-op.
  const canonical = JSON.stringify(parsed.data, Object.keys(
    flatten(parsed.data)).sort());
  await db().execute({
    sql: `INSERT INTO routes (route_id, config_json, created_at, updated_at)
          VALUES (?, ?, ?, ?)
          ON CONFLICT(route_id) DO UPDATE SET
              config_json = excluded.config_json,
              updated_at  = excluded.updated_at`,
    args: [ROUTE_ID, canonical, now, now],
  });
  return NextResponse.json({ ok: true, updatedAt: now });
}

/** Collect every key at every depth (JSON.stringify's replacer-array
 *  applies to nested objects too). */
function flatten(obj: unknown, keys: Set<string> = new Set()): Record<string, true> {
  if (obj && typeof obj === "object" && !Array.isArray(obj)) {
    for (const [k, v] of Object.entries(obj)) {
      keys.add(k);
      flatten(v, keys);
    }
  } else if (Array.isArray(obj)) {
    for (const v of obj) flatten(v, keys);
  }
  return Object.fromEntries([...keys].map((k) => [k, true])) as Record<string, true>;
}
