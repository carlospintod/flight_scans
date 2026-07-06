import { NextResponse, type NextRequest } from "next/server";
import { isAuthed } from "@/lib/auth";

const REPO = "carlospintod/flight_scans";
const WORKFLOW = "scan.yml";

/** Fires the GitHub Actions scan via workflow_dispatch. The fine-grained
 *  PAT (Actions read+write, this repo only) lives server-side in
 *  GH_WORKFLOW_TOKEN — the browser never sees it. */
export async function POST(req: NextRequest) {
  if (!(await isAuthed())) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const token = process.env.GH_WORKFLOW_TOKEN ?? "";
  if (!token) {
    return NextResponse.json(
      { error: "GH_WORKFLOW_TOKEN not configured on the server" },
      { status: 501 },
    );
  }
  let inputs: Record<string, string> = {};
  try {
    const body = await req.json();
    if (body?.sources) inputs.sources = String(body.sources);
    if (body?.cap) inputs.cap = String(body.cap);
  } catch {
    inputs = {};
  }
  const r = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref: "main", inputs }),
      cache: "no-store",
    },
  );
  if (r.status !== 204) {
    const text = await r.text();
    return NextResponse.json(
      { error: `GitHub returned ${r.status}: ${text.slice(0, 200)}` },
      { status: 502 },
    );
  }
  return NextResponse.json({
    ok: true,
    runsUrl: `https://github.com/${REPO}/actions/workflows/${WORKFLOW}`,
  });
}
