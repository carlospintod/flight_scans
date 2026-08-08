import { NextResponse } from "next/server";
import { MEMBER_COOKIE } from "@/lib/member-auth";

export async function POST() {
  const res = NextResponse.json({ ok: true });
  res.cookies.set(MEMBER_COOKIE, "", { path: "/", maxAge: 0 });
  return res;
}
