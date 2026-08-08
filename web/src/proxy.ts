import { NextResponse, type NextRequest } from "next/server";
import {
  SESSION_COOKIE,
  verifySessionValue,
  verifyUserSessionValue,
} from "@/lib/auth";

/** Two jobs, both stateless:
 *
 *  1. Brand front door: when the request arrives on the vuelazo.es host,
 *     "/" rewrites to the Vuelazo landing (/vuelazo). The tracker keeps
 *     "/" on every other host. URL stays "/" in the browser.
 *  2. Gate authed tracker pages (crypto-only, zero DB reads per
 *     navigation): a valid v2 user session OR the legacy APP_PASSWORD
 *     break-glass session passes; role/ownership checks happen inside
 *     the pages/handlers (they have DB access).
 *
 *  Next 16 renamed the middleware convention to proxy. */
export async function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (pathname === "/") {
    const host = req.headers.get("host") ?? "";
    if (host === "vuelazo.es" || host.endsWith(".vuelazo.es")) {
      const url = req.nextUrl.clone();
      url.pathname = "/vuelazo";
      return NextResponse.rewrite(url);
    }
    return NextResponse.next();
  }

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
  matcher: ["/", "/ops/:path*", "/searches/:path*", "/account/:path*"],
};
