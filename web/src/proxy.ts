import { NextResponse, type NextRequest } from "next/server";
import {
  SESSION_COOKIE,
  verifySessionValue,
  verifyUserSessionValue,
} from "@/lib/auth";

/** Gate authed pages. Stateless (crypto-only, zero DB reads per
 *  navigation): a valid v2 user session OR the legacy APP_PASSWORD
 *  break-glass session passes; role/ownership checks happen inside the
 *  pages/handlers (they have DB access). Next 16 renamed the middleware
 *  convention to proxy. */
export async function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (pathname === "/ops/login") return NextResponse.next();
  const raw = req.cookies.get(SESSION_COOKIE)?.value;
  const userSession = await verifyUserSessionValue(raw);
  const breakGlass = userSession ? false : await verifySessionValue(raw);
  if (userSession || breakGlass) return NextResponse.next();
  const url = req.nextUrl.clone();
  url.pathname = pathname.startsWith("/ops") ? "/ops/login" : "/join";
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/ops/:path*", "/searches/:path*", "/account/:path*"],
};
