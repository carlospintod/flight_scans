// Deal-queue actions (D3): approve / reject-with-reason / edit.
//
// Transitions are single-statement CAS (status guarded in the WHERE) —
// the Turso HTTP path is autocommit per statement. Approving also
// best-effort dispatches the deals workflow in publish-only mode so the
// fan-out happens within a minute; without GH_WORKFLOW_TOKEN the deal
// simply waits for the next cron sweep (never lost, only slower).

import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { db } from "@/lib/db";
import { isOpsBreakGlass } from "@/lib/auth";
import { requireUser } from "@/lib/users";

const REPO = "carlospintod/flight_scans";
const WORKFLOW = "deals.yml";

const bodySchema = z.object({
  action: z.enum(["approve", "reject", "edit"]),
  reason: z
    .enum(["too_common", "bad_dates", "ulcc_junk", "thin_saving", "other"])
    .optional(),
  note: z.string().max(500).optional(),
  draft_es: z.string().max(4000).optional(),
  free_pick: z.boolean().optional(),
});

async function authorized(): Promise<boolean> {
  if (await requireUser("owner")) return true;
  return isOpsBreakGlass();
}

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

async function dispatchPublish(): Promise<boolean> {
  const token = process.env.GH_WORKFLOW_TOKEN ?? "";
  if (!token) return false;
  try {
    const r = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({ ref: "main", inputs: { publish_only: "true" } }),
        cache: "no-store",
      },
    );
    return r.status === 204;
  } catch {
    return false;
  }
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  if (!(await authorized())) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const { id } = await params;
  const dealId = Number(id);
  if (!Number.isInteger(dealId) || dealId <= 0) {
    return NextResponse.json({ error: "bad id" }, { status: 400 });
  }
  let body: z.infer<typeof bodySchema>;
  try {
    body = bodySchema.parse(await req.json());
  } catch {
    return NextResponse.json({ error: "bad body" }, { status: 400 });
  }

  if (body.action === "edit") {
    if (!body.draft_es?.trim()) {
      return NextResponse.json({ error: "draft_es required" }, { status: 400 });
    }
    const rs = await db().execute({
      sql: `UPDATE deals SET draft_es = ?
            WHERE id = ? AND status IN ('queued', 'approved')`,
      args: [body.draft_es.trim(), dealId],
    });
    if (rs.rowsAffected !== 1) {
      return NextResponse.json({ error: "deal not editable" }, { status: 409 });
    }
    return NextResponse.json({ ok: true });
  }

  if (body.action === "reject") {
    const reason = body.reason ?? "other";
    const rs = await db().execute({
      sql: `UPDATE deals SET status = 'rejected'
            WHERE id = ? AND status = 'queued'`,
      args: [dealId],
    });
    if (rs.rowsAffected !== 1) {
      return NextResponse.json({ error: "deal not in queue" }, { status: 409 });
    }
    await db().execute({
      sql: `INSERT INTO rejections (deal_id, reason, note, ts)
            VALUES (?, ?, ?, ?)`,
      args: [dealId, reason, body.note ?? null, nowIso()],
    });
    return NextResponse.json({ ok: true });
  }

  // approve (optionally with an edited draft in the same tap)
  if (body.draft_es?.trim()) {
    await db().execute({
      sql: `UPDATE deals SET draft_es = ? WHERE id = ? AND status = 'queued'`,
      args: [body.draft_es.trim(), dealId],
    });
  }
  const rs = await db().execute({
    sql: `UPDATE deals SET status = 'approved', approved_at = ?, free_pick = ?
          WHERE id = ? AND status = 'queued'`,
    args: [nowIso(), body.free_pick ? 1 : 0, dealId],
  });
  if (rs.rowsAffected !== 1) {
    return NextResponse.json({ error: "deal not in queue" }, { status: 409 });
  }
  const dispatched = await dispatchPublish();
  return NextResponse.json({ ok: true, dispatched });
}
