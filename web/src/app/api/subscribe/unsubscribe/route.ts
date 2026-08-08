// One-click unsubscribe for FREE-LIST subscribers (RFC 8058).
// POST executes (providers call it deliberately); GET only renders a
// confirm form — mail scanners prefetch GETs and must not be able to
// silently unsubscribe people.

import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

async function unsub(token: string): Promise<boolean> {
  if (!/^[0-9a-f]{48}$/.test(token)) return false;
  try {
    const rs = await db().execute({
      sql: "SELECT email FROM subscribers WHERE unsub_token = ?",
      args: [token],
    });
    if (!rs.rows[0]) return false;
    const email = String(rs.rows[0]["email"]);
    await db().execute({
      sql: "UPDATE subscribers SET status = 'unsub' WHERE email = ?",
      args: [email],
    });
    await db().execute({
      sql: `INSERT OR REPLACE INTO suppressions (email, reason, ts)
            VALUES (?, 'subscriber_unsub', ?)`,
      args: [email, nowIso()],
    });
    return true;
  } catch {
    return false;
  }
}

export async function POST(req: NextRequest) {
  const ok = await unsub(req.nextUrl.searchParams.get("token") ?? "");
  const isForm = (req.headers.get("content-type") ?? "").includes("form");
  if (isForm) {
    const msg = ok
      ? "Listo: fuera de la lista. Puedes volver cuando quieras en vuelazo.es."
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
  const token = req.nextUrl.searchParams.get("token") ?? "";
  const valid = /^[0-9a-f]{48}$/.test(token);
  const inner = valid
    ? `<p>¿Quieres dejar de recibir el resumen semanal de Vuelazo?</p>` +
      `<form method="POST" action="/api/subscribe/unsubscribe?token=${token}">` +
      `<button type="submit" style="font:inherit;padding:10px 18px;` +
      `background:#d97706;color:#faf6ef;border:0;border-radius:6px;` +
      `cursor:pointer">Sí, darme de baja</button></form>`
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
