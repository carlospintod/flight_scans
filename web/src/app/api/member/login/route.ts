// Magic-link request for members (M2). Always answers ok (no account
// enumeration); rate-limited per IP in Turso like /api/login.

import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { db } from "@/lib/db";
import { sendEmail } from "@/lib/email";
import {
  ensureMemberTables,
  getMemberByEmail,
  isSuppressed,
  mintMemberToken,
} from "@/lib/members";

const WINDOW_MS = 15 * 60 * 1000;
const MAX_ATTEMPTS = 6;

export async function POST(req: NextRequest) {
  const ip =
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
  const client = db();
  // Scoped rate-limit bucket: sharing the tracker's login_attempts table
  // would let free-list signups burn a member's login quota (same NAT).
  await client.execute(
    `CREATE TABLE IF NOT EXISTS login_attempts_member (
       ip TEXT NOT NULL, attempted_at INTEGER NOT NULL)`,
  );
  const rs = await client.execute({
    sql: "SELECT COUNT(*) AS n FROM login_attempts_member WHERE ip = ? AND attempted_at >= ?",
    args: [ip, Date.now() - WINDOW_MS],
  });
  if (Number(rs.rows[0]?.["n"] ?? 0) >= MAX_ATTEMPTS) {
    return NextResponse.json(
      { error: "demasiados intentos — prueba en 15 minutos" },
      { status: 429 },
    );
  }
  await client.execute({
    sql: "INSERT INTO login_attempts_member (ip, attempted_at) VALUES (?, ?)",
    args: [ip, Date.now()],
  });
  await client.execute({
    sql: "DELETE FROM login_attempts_member WHERE attempted_at < ?",
    args: [Date.now() - 24 * 3600 * 1000],
  });

  let email = "";
  try {
    email = z.object({ email: z.string().email() }).parse(await req.json()).email;
  } catch {
    return NextResponse.json({ error: "email inválido" }, { status: 400 });
  }

  await ensureMemberTables();
  const member = await getMemberByEmail(email);
  // Deliberately NOT suppression-gated: this mail is solicited by the
  // member seconds ago and carries account access — suppression governs
  // BULK sends (alerts/digest/reminders), see lib/email.ts contract.
  if (member) {
    const token = await mintMemberToken(member.id, "login", 1);
    await sendEmail({
      to: member.email,
      subject: "Tu enlace de acceso a Vuelazo",
      text:
        `Entra en tu cuenta (el enlace funciona una vez, caduca en 24h):\n\n` +
        `${req.nextUrl.origin}/cuenta#${token}\n\n` +
        `Si no lo has pedido tú, ignora este email.\nVuelazo`,
    });
  }
  // Same answer with or without an account.
  return NextResponse.json({ ok: true });
}
