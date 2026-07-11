import { redirect } from "next/navigation";
import { Card, SectionHeading } from "@/components/Section";
import { getUserSession } from "@/lib/auth";
import { getUser } from "@/lib/users";
import { AccountActions } from "@/components/AccountActions";

export const dynamic = "force-dynamic";

export default async function AccountPage() {
  const session = await getUserSession();
  if (!session) redirect("/join");
  const user = await getUser(session.userId);
  if (!user) redirect("/join");

  return (
    <div className="max-w-lg space-y-8">
      <h1 className="font-mono text-lg text-text-bright">ACCOUNT</h1>
      <Card>
        <dl className="space-y-2 font-mono text-[13px]">
          <div>
            <dt className="text-[10px] uppercase tracking-wider text-hint">
              name
            </dt>
            <dd className="text-text-bright">{user.displayName ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-[10px] uppercase tracking-wider text-hint">
              email (contact only — login is by link)
            </dt>
            <dd className="text-text">{user.email ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-[10px] uppercase tracking-wider text-hint">
              role
            </dt>
            <dd className="text-text">{user.role}</dd>
          </div>
        </dl>
      </Card>
      <section>
        <SectionHeading>Session & data</SectionHeading>
        <AccountActions isOwner={user.role === "owner"} />
      </section>
      <p className="font-mono text-[11px] leading-5 text-hint">
        Lost your session on another device? Ask the owner for a fresh
        login link. Deleting your account removes your searches and every
        price row collected for them — permanently.
      </p>
    </div>
  );
}
