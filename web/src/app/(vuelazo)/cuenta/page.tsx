import type { Metadata } from "next";
import { db } from "@/lib/db";
import CuentaPanel from "@/components/member/CuentaPanel";
import { getSessionMember } from "@/lib/members";

export const metadata: Metadata = { title: "Tu cuenta — Vuelazo" };
export const dynamic = "force-dynamic";

export default async function CuentaPage() {
  const member = await getSessionMember();
  let suppressed = false;
  if (member) {
    try {
      const rs = await db().execute({
        sql: "SELECT 1 FROM suppressions WHERE email = ?",
        args: [member.email],
      });
      suppressed = rs.rows.length > 0;
    } catch {
      suppressed = false;
    }
  }
  return (
    <CuentaPanel
      member={
        member
          ? {
              id: member.id,
              email: member.email,
              status: member.status,
              memberUntil: member.memberUntil,
              plan: member.plan,
              telegramBound: member.telegramUserId != null,
              airports: member.airports,
              suppressed,
            }
          : null
      }
    />
  );
}
