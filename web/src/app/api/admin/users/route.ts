import { NextResponse, type NextRequest } from "next/server";
import { isOpsBreakGlass } from "@/lib/auth";
import {
  listUsers,
  mintLinkToken,
  requireUser,
  upsertInvitee,
} from "@/lib/users";

/** Owner-only user administration: list users, invite (create + mint an
 *  invite link), or mint a re-login link for an existing user. The RAW
 *  token returns to the OWNER'S browser once, to be shared manually
 *  (WhatsApp) — link-based auth, no email dependency. */

async function authorized(): Promise<number | null> {
  const user = await requireUser("owner");
  if (user) return user.userId;
  // Break-glass APP_PASSWORD session also administers (bootstrap path:
  // the owner's own first login link is minted from here).
  if (await isOpsBreakGlass()) return 1;
  return null;
}

export async function GET() {
  if ((await authorized()) === null) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  return NextResponse.json({ users: await listUsers() });
}

export async function POST(req: NextRequest) {
  const adminId = await authorized();
  if (adminId === null) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  let body: Record<string, unknown> = {};
  try {
    body = await req.json();
  } catch {
    /* validated below */
  }
  let userId: number;
  let purpose: "invite" | "login";
  if (typeof body.userId === "number") {
    userId = body.userId;
    purpose = "login";
  } else if (typeof body.email === "string" && body.email.includes("@")) {
    userId = await upsertInvitee(
      body.email.trim().toLowerCase(),
      String(body.displayName ?? "").trim() || body.email.split("@")[0],
      adminId,
    );
    purpose = "invite";
  } else {
    return NextResponse.json({ error: "email or userId required" },
                             { status: 400 });
  }
  const token = await mintLinkToken(userId, purpose);
  // Fragment (#) keeps the token out of server/proxy logs everywhere.
  return NextResponse.json({
    ok: true, userId, purpose, joinPath: `/join#${token}`,
  });
}
