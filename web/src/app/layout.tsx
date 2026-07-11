import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import Link from "next/link";
import "./globals.css";

export const viewport: Viewport = {
  themeColor: "#181825",
};

const plexMono = IBM_Plex_Mono({
  weight: ["400", "500", "600"],
  subsets: ["latin"],
  variable: "--font-plex-mono",
});

const plexSans = IBM_Plex_Sans({
  weight: ["400", "500", "600"],
  subsets: ["latin"],
  variable: "--font-plex-sans",
});

export const metadata: Metadata = {
  title: "flight_scans — MAD/BCN → NBO price radar",
  description:
    "Flexible-date flight price tracker: departure and return both float " +
    "inside a window, bound by min/max stay. Live data scanned by GitHub " +
    "Actions. $0/month stack.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // suppressHydrationWarning: the inline script in <body> adds the `js`
    // class to <html> before hydration (theme-script pattern)
    <html
      lang="en"
      suppressHydrationWarning
      className={`${plexMono.variable} ${plexSans.variable}`}
    >
      <body className="min-h-screen antialiased">
        {/* reveal-gate: phosphor.css hides .reveal only under html.js, so
            content never flashes (and never strands if phosphor.js fails —
            it force-reveals 1.4s after init as a second net) */}
        <script
          dangerouslySetInnerHTML={{
            __html: 'document.documentElement.classList.add("js")',
          }}
        />
        <header className="border-b border-border bg-bg2">
          <nav className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-y-1 px-4 py-3">
            <Link
              href="/"
              className="cursor-blink font-mono text-sm font-semibold tracking-[2px] text-text-bright"
            >
              FLIGHT_SCANS
            </Link>
            <div className="flex flex-wrap items-center gap-x-5 gap-y-1 font-mono text-xs tracking-wider text-text-mid">
              <Link href="/" className="hover:text-signature">
                RADAR
              </Link>
              <Link href="/searches" className="hover:text-signature">
                SEARCHES
              </Link>
              <Link href="/about" className="hover:text-signature">
                ABOUT
              </Link>
              <a
                href="https://github.com/carlospintod/flight_scans"
                className="hover:text-signature"
                target="_blank"
                rel="noreferrer"
              >
                GITHUB
              </a>
              <Link
                href="/ops"
                className="text-hint hover:text-signature"
                title="Operator console (login required)"
              >
                OPS
              </Link>
            </div>
          </nav>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
        <footer className="mx-auto mt-8 max-w-6xl border-t border-border px-4 py-6 font-mono text-[11px] text-hint">
          Not a booking site. Prices are observations from free sources and
          can be stale — verify on the airline or Google Flights before
          booking.
        </footer>
      </body>
    </html>
  );
}
