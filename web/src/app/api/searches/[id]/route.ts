import { NextResponse, type NextRequest } from "next/server";
import { db } from "@/lib/db";
import { requireUser } from "@/lib/users";

const SLUG = /^[a-z0-9-]{1,40}$/;
const NOTIFY = new Set(["every_run", "alerts_only", "off"]);

/** Owner-of-search controls: pause/resume and the notify mode. Paused
 *  searches keep their data and their capacity slot frees immediately
 *  (the batch runner only enumerates status='active'). */
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const user = await requireUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const { id } = await params;
  if (!SLUG.test(id)) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }
  const rs = await db().execute({
    sql: "SELECT user_id, status FROM searches WHERE search_id = ?",
    args: [id],
  });
  const row = rs.rows[0];
  if (!row || (Number(row["user_id"]) !== user.userId
               && user.role !== "owner")) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }

  let body: Record<string, unknown> = {};
  try {
    body = await req.json();
  } catch { /* validated below */ }

  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  if (body.action === "pause" || body.action === "resume") {
    const next = body.action === "pause" ? "paused" : "active";
    await db().execute({
      sql: `UPDATE searches SET status = ?, updated_at = ?
            WHERE search_id = ? AND status != 'ended'`,
      args: [next, now, id],
    });
    return NextResponse.json({ ok: true, status: next });
  }
  if (typeof body.notify === "string" && NOTIFY.has(body.notify)) {
    await db().execute({
      sql: "UPDATE searches SET notify = ?, updated_at = ? WHERE search_id = ?",
      args: [body.notify, now, id],
    });
    return NextResponse.json({ ok: true, notify: body.notify });
  }
  return NextResponse.json(
    { error: "expected {action: pause|resume} or {notify: ...}" },
    { status: 400 },
  );
}
