import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import Link from "next/link";
import "./globals.css";

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
    <html lang="en" className={`${plexMono.variable} ${plexSans.variable}`}>
      <body className="min-h-screen antialiased">
        <header className="border-b border-line bg-bg-2">
          <nav className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
            <Link
              href="/"
              className="cursor-blink font-mono text-sm font-semibold tracking-[2px] text-fg-bright"
            >
              FLIGHT_SCANS
            </Link>
            <div className="flex items-center gap-5 font-mono text-xs tracking-wider text-fg-mid">
              <Link href="/" className="hover:text-matrix">
                RADAR
              </Link>
              <Link href="/about" className="hover:text-matrix">
                ABOUT
              </Link>
              <a
                href="https://github.com/carlospintod/flight_scans"
                className="hover:text-matrix"
                target="_blank"
                rel="noreferrer"
              >
                GITHUB
              </a>
            </div>
          </nav>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
        <footer className="mx-auto mt-8 max-w-6xl border-t border-line px-4 py-6 font-mono text-[11px] text-fg-dim">
          Not a booking site. Prices are observations from free sources and
          can be stale — verify on the airline or Google Flights before
          booking.
        </footer>
      </body>
    </html>
  );
}
