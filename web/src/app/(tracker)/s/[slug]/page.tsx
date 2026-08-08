import { notFound } from "next/navigation";
import { SearchRadar } from "@/components/SearchRadar";
import { getUserSession, isOpsBreakGlass } from "@/lib/auth";
import { db } from "@/lib/db";
import { getRouteWindow } from "@/lib/queries";

export const dynamic = "force-dynamic";

const SLUG = /^[a-z0-9-]{1,40}$/;

/** Per-search results — the same renderer as the public demo. Access:
 *  the search is public, OR you own it, OR you're the operator.
 *  Strangers get 404 (not 403): private searches don't exist for them. */
export default async function SearchPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  if (!SLUG.test(slug)) notFound();

  const rs = await db().execute({
    sql: "SELECT user_id, is_public, status FROM searches WHERE search_id = ?",
    args: [slug],
  });
  const row = rs.rows[0];
  if (!row) notFound();

  const isPublic = Number(row["is_public"]) === 1;
  if (!isPublic) {
    const session = await getUserSession();
    const owns = session && session.userId === Number(row["user_id"]);
    if (!owns && !(await isOpsBreakGlass())) notFound();
  }

  let w;
  try {
    w = await getRouteWindow(slug);
  } catch {
    notFound();
  }
  return <SearchRadar w={w} />;
}
