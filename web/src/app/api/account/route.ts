import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/auth";
import { deleteUserCascade, requireUser } from "@/lib/users";

/** Self-service account deletion (GDPR path): removes the user, their
 *  searches, and every data row keyed to them — ordered, re-runnable.
 *  The owner account cannot self-delete (it owns the mission search). */
export async function DELETE() {
  const user = await requireUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  if (user.role === "owner") {
    return NextResponse.json(
      { error: "the owner account cannot be deleted from the UI" },
      { status: 403 },
    );
  }
  await deleteUserCascade(user.userId);
  const res = NextResponse.json({ ok: true, deleted: true });
  res.cookies.set(SESSION_COOKIE, "", { maxAge: 0, path: "/" });
  return res;
}
