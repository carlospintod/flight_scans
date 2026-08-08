// Consume a member magic-link token -> set the member session cookie.
// Token travels in the URL fragment and is only spent on an explicit
// POST (the /join pattern: scanners' GETs can't burn it).

import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import {
  MEMBER_COOKIE,
  makeMemberSessionValue,
  memberCookieOptions,
} from "@/lib/member-auth";
import { consumeMemberToken, ensureMemberTables } from "@/lib/members";

export async function POST(req: NextRequest) {
  let token = "";
  try {
    token = z
      .object({ token: z.string().regex(/^[0-9a-f]{48}$/) })
      .parse(await req.json()).token;
  } catch {
    return NextResponse.json({ error: "token inválido" }, { status: 400 });
  }
  await ensureMemberTables();
  const memberId = await consumeMemberToken(token, "login");
  if (memberId == null) {
    return NextResponse.json(
      { error: "enlace caducado o ya usado — pide otro" },
      { status: 401 },
    );
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(
    MEMBER_COOKIE,
    await makeMemberSessionValue(memberId),
    memberCookieOptions(),
  );
  return res;
}
