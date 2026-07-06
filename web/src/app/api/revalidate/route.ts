import { revalidatePath } from "next/cache";
import { NextResponse, type NextRequest } from "next/server";

/** Called by scan.yml after each run so the radar is fresh within
 *  seconds of new data (ISR's 6h is only the safety net). Secret-gated:
 *  the caller is a GitHub Actions step, not a browser. */
export async function POST(req: NextRequest) {
  const secret = process.env.REVALIDATE_SECRET ?? "";
  const given = req.nextUrl.searchParams.get("secret") ?? "";
  if (!secret || given !== secret) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  revalidatePath("/", "layout"); // radar + drill-downs + about
  return NextResponse.json({ ok: true, revalidated: true });
}
