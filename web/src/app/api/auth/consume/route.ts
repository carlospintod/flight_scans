import { NextResponse, type NextRequest } from "next/server";
import {
  SESSION_COOKIE,
  makeUserSessionValue,
  sessionCookieOptions,
} from "@/lib/auth";
import { consumeLinkToken, getUser } from "@/lib/users";

/** Consume a one-time invite/login token (POSTed from the /join page —
 *  tokens live in URL fragments and are never consumed on GET, so mail/
 *  chat link-scanners can't burn them). Single-statement CAS: exactly
 *  one click wins. */
export async function POST(req: NextRequest) {
  let token = "";
  try {
    const body = await req.json();
    token = String(body?.token ?? "");
  } catch {
    /* fall through */
  }
  if (!/^[0-9a-f]{48}$/.test(token)) {
    return NextResponse.json({ error: "malformed link" }, { status: 400 });
  }
  const consumed = await consumeLinkToken(token);
  if (!consumed) {
    return NextResponse.json(
      { error: "link expired or already used — ask for a fresh one" },
      { status: 410 },
    );
  }
  const user = await getUser(consumed.userId);
  if (!user) {
    return NextResponse.json({ error: "account no longer exists" },
                             { status: 410 });
  }
  const res = NextResponse.json({ ok: true, displayName: user.displayName });
  res.cookies.set(
    SESSION_COOKIE,
    await makeUserSessionValue(user.userId, user.sessionVersion),
    sessionCookieOptions(),
  );
  return res;
}
