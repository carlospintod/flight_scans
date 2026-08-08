// Vuelazo root layout (M4a, D7): editorial data-design — warm paper,
// near-black ink, one amber signal, serif display (Fraunces) + Inter
// text. The deliberate opposite of the tracker's phosphor dark (which
// keeps its own root layout in the (tracker) group).

import type { Metadata, Viewport } from "next";
import { Fraunces, Inter } from "next/font/google";
import Link from "next/link";
import "../globals.css";

const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-fraunces",
  display: "swap",
});
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Vuelazo — vuelazos desde tu aeropuerto",
  description:
    "Chollos de vuelo verificados desde València, Alacant, Madrid y " +
    "Barcelona. El precio normal, demostrado — y el chollo, a tiempo.",
};

export const viewport: Viewport = { themeColor: "#faf6ef" };

export default function VuelazoLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es" className={`${fraunces.variable} ${inter.variable}`}>
      <body className="min-h-screen bg-vz-paper font-vz-text text-vz-ink antialiased">
        <header className="border-b border-vz-line">
          <div className="mx-auto flex max-w-5xl flex-wrap items-baseline gap-x-6 gap-y-2 px-5 py-5">
            <Link
              href="/vuelazo"
              className="font-vz-display text-2xl font-semibold tracking-tight"
            >
              Vuelazo<span className="text-vz-amber">.</span>
            </Link>
            <nav className="flex flex-wrap gap-x-5 gap-y-1 text-[14px] text-vz-ink-soft">
              <Link href="/vuelazos" className="hover:text-vz-ink">
                chollos
              </Link>
              <Link href="/vuelos-baratos/valencia" className="hover:text-vz-ink">
                desde tu aeropuerto
              </Link>
              <Link href="/cuenta" className="hover:text-vz-ink">
                tu cuenta
              </Link>
            </nav>
            <Link
              href="/unete"
              className="ml-auto rounded-md bg-vz-amber px-4 py-1.5 text-[14px] font-semibold text-vz-paper hover:bg-vz-amber-deep"
            >
              Únete →
            </Link>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-5 py-10">{children}</main>
        <footer className="border-t border-vz-line">
          <div className="mx-auto flex max-w-5xl flex-wrap gap-x-6 gap-y-2 px-5 py-8 text-[12px] text-vz-ink-soft">
            <span>© {new Date().getFullYear()} Vuelazo</span>
            <Link href="/aviso-legal" className="hover:text-vz-ink">
              aviso legal
            </Link>
            <Link href="/privacidad" className="hover:text-vz-ink">
              privacidad
            </Link>
            <Link href="/condiciones" className="hover:text-vz-ink">
              condiciones
            </Link>
            <span className="ml-auto">
              No ganamos nada con tus clics — solo con tu membresía.
            </span>
          </div>
        </footer>
      </body>
    </html>
  );
}
