// One-click unsubscribe (RFC 8058) — the List-Unsubscribe target on
// every bulk mail (non-negotiable #7). POST is the one-click path mail
// providers call deliberately. GET only renders a CONFIRM page whose
// button POSTs — mail scanners (SafeLinks/Proofpoint) prefetch GET
// links in bodies, and an auto-executing GET would silently suppress
// paying members (the same reason /join keeps tokens in the fragment).

import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { sha256Hex } from "@/lib/member-auth";
import { ensureMemberTables, getMember, logMemberEvent, nowIso } from "@/lib/members";

async function unsubscribe(token: string): Promise<boolean> {
  if (!/^[0-9a-f]{48}$/.test(token)) return false;
  await ensureMemberTables();
  const hash = await sha256Hex(token);
  // Look the token up regardless of consumed state: unsubscribing twice
  // must succeed silently (mail providers retry POSTs).
  const rs = await db().execute({
    sql: `SELECT member_id FROM member_tokens
          WHERE token_hash = ? AND purpose = 'unsub'`,
    args: [hash],
  });
  if (!rs.rows[0]) return false;
  const memberId = Number(rs.rows[0]["member_id"]);
  const member = await getMember(memberId);
  if (!member) return false;
  await db().execute({
    sql: `INSERT OR REPLACE INTO suppressions (email, reason, ts)
          VALUES (?, 'one_click_unsubscribe', ?)`,
    args: [member.email, nowIso()],
  });
  await db().execute({
    sql: "UPDATE member_tokens SET consumed_at = ? WHERE token_hash = ? AND consumed_at IS NULL",
    args: [nowIso(), hash],
  });
  await logMemberEvent(memberId, "unsubscribed");
  return true;
}

export async function POST(req: NextRequest) {
  const ok = await unsubscribe(req.nextUrl.searchParams.get("token") ?? "");
  // The confirm-page form POSTs here too — answer humans with HTML.
  const isForm = (req.headers.get("content-type") ?? "").includes("form");
  if (isForm) {
    const msg = ok
      ? "Listo: no recibirás más emails de Vuelazo."
      : "Enlace de baja no válido.";
    return new NextResponse(
      `<!doctype html><meta charset="utf-8"><title>Vuelazo</title>` +
        `<body style="font-family:system-ui;background:#faf6ef;color:#1c1917;` +
        `display:grid;place-items:center;min-height:100vh"><p>${msg}</p></body>`,
      { status: ok ? 200 : 400, headers: { "Content-Type": "text/html" } },
    );
  }
  return NextResponse.json({ ok }, { status: ok ? 200 : 400 });
}

export async function GET(req: NextRequest) {
  // No side effect on GET (scanner prefetch safety) — render a confirm
  // form that POSTs the token to this same route.
  const token = req.nextUrl.searchParams.get("token") ?? "";
  const valid = /^[0-9a-f]{48}$/.test(token);
  const inner = valid
    ? `<p>¿Quieres dejar de recibir emails de Vuelazo?</p>` +
      `<form method="POST" action="/api/unsubscribe?token=${token}">` +
      `<button type="submit" style="font:inherit;padding:10px 18px;` +
      `background:#d97706;color:#faf6ef;border:0;border-radius:6px;` +
      `cursor:pointer">Sí, darme de baja</button></form>` +
      `<p style="font-size:12px;color:#6e6860">Puedes reactivarlos en tu ` +
      `cuenta (/cuenta) cuando quieras.</p>`
    : `<p>Enlace de baja no válido.</p>`;
  return new NextResponse(
    `<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">` +
      `<title>Vuelazo</title>` +
      `<body style="font-family:system-ui;background:#faf6ef;color:#1c1917;` +
      `display:grid;place-items:center;min-height:100vh;text-align:center">` +
      `<div>${inner}</div></body>`,
    { status: valid ? 200 : 400, headers: { "Content-Type": "text/html" } },
  );
}
