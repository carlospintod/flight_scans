// Per-airport alert preference (M2, D4 personalization layer).

import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { db } from "@/lib/db";
import { getSessionMember, logMemberEvent } from "@/lib/members";

const VALID = ["MAD", "BCN", "VLC", "ALC"] as const;

export async function PATCH(req: NextRequest) {
  const member = await getSessionMember();
  if (!member) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  let airports: string[];
  try {
    airports = z
      .object({ airports: z.array(z.enum(VALID)).min(1) })
      .parse(await req.json()).airports;
  } catch {
    return NextResponse.json(
      { error: "elige al menos un aeropuerto" },
      { status: 400 },
    );
  }
  const unique = [...new Set(airports)];
  await db().execute({
    sql: "UPDATE members SET airports = ? WHERE id = ?",
    args: [JSON.stringify(unique), member.id],
  });
  await logMemberEvent(member.id, "airports_set", unique.join(","));
  return NextResponse.json({ ok: true, airports: unique });
}
