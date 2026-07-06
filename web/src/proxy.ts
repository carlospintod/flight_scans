import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, verifySessionValue } from "@/lib/auth";

/** Gate /ops pages: no valid session -> login. Mutating API routes do
 *  their own isAuthed() check in-handler (401 JSON, not a redirect).
 *  Next 16 renamed the middleware convention to proxy. */
export async function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (pathname === "/ops/login") return NextResponse.next();
  const ok = await verifySessionValue(req.cookies.get(SESSION_COOKIE)?.value);
  if (!ok) {
    const url = req.nextUrl.clone();
    url.pathname = "/ops/login";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/ops/:path*"],
};
