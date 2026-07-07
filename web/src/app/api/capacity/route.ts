import { NextResponse } from "next/server";
import { capacityView } from "@/lib/capacity";
import { requireUser } from "@/lib/users";

export const dynamic = "force-dynamic";

export async function GET() {
  if (!(await requireUser())) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  return NextResponse.json(await capacityView());
}
