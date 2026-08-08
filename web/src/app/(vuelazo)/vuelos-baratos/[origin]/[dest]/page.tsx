// Gated SEO route pages (M4b, D6): /vuelos-baratos/valencia/roma.
// Generated from OUR data: price history (inline SVG — no client JS),
// provable "precio normal", recent best fares, data-derived booking
// window, free-alerts CTA. Quality gate: a page is INDEXED only when
// the route passes the detector's own min_observations bar; thin routes
// stay noindex until data matures. Claude intros arrive via
// scripts/gen_seo_intros.py into seo_pages (once, quarterly refresh);
// until then the page runs on data blocks alone.

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { db } from "@/lib/db";
import SubscribeForm from "@/components/member/SubscribeForm";
import { HUBS, destName } from "@/lib/hubs";

export const revalidate = 86400; // nightly SSG (ISR)

// Mirrors routes/vuelazo.yaml detector.min_observations — the SEO gate
// IS the detector's maturity bar (D6).
const MIN_OBSERVATIONS = 8;
const WINDOW_DAYS = 60;

type RouteStats = {
  verifiedCount: number;
  baselineMedian: number | null;
  cachedMedian: number | null;
  history: { day: string; price: number }[];
  best: { price: number; dep: string; ret: string | null; day: string }[];
  bestMonth: string | null;
};

function median(xs: number[]): number | null {
  if (xs.length === 0) return null;
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : Math.round((s[mid - 1] + s[mid]) / 2);
}

// NOTE: this THROWS on DB failure (no catch) on purpose — a transient
// Turso error during ISR regeneration must keep serving the stale good
// page, not cache a 404 / flip a mature page to noindex for 24h.
async function routeStats(origin: string, dest: string): Promise<RouteStats> {
  const since = new Date(Date.now() - WINDOW_DAYS * 864e5)
    .toISOString()
    .slice(0, 10);
  {
    const [verified, history, best, months] = await Promise.all([
      db().execute({
        sql: `SELECT price FROM fare_observations
              WHERE origin = ? AND dest = ? AND is_verified = 1
                AND observed_at >= ?`,
        args: [origin, dest, since],
      }),
      db().execute({
        sql: `SELECT substr(observed_at,1,10) AS day, MIN(price) AS price
              FROM fare_observations
              WHERE origin = ? AND dest = ? AND observed_at >= ?
              GROUP BY day ORDER BY day`,
        args: [origin, dest, since],
      }),
      db().execute({
        sql: `SELECT price, depart_date, return_date,
                     substr(observed_at,1,10) AS day
              FROM fare_observations
              WHERE origin = ? AND dest = ? AND observed_at >= ?
              ORDER BY price LIMIT 5`,
        args: [origin, dest,
               new Date(Date.now() - 14 * 864e5).toISOString().slice(0, 10)],
      }),
      db().execute({
        sql: `SELECT substr(depart_date,1,7) AS m, MIN(price) AS p
              FROM fare_observations
              WHERE origin = ? AND dest = ? AND observed_at >= ?
              GROUP BY m HAVING COUNT(*) >= 3 ORDER BY p LIMIT 1`,
        args: [origin, dest, since],
      }),
    ]);
    const verifiedPrices = verified.rows.map((r) => Number(r["price"]));
    const cachedPrices = history.rows.map((r) => Number(r["price"]));
    return {
      verifiedCount: verifiedPrices.length,
      baselineMedian: median(verifiedPrices),
      cachedMedian: median(cachedPrices),
      history: history.rows.map((r) => ({
        day: String(r["day"]),
        price: Number(r["price"]),
      })),
      best: best.rows.map((r) => ({
        price: Number(r["price"]),
        dep: String(r["depart_date"]),
        ret: r["return_date"] ? String(r["return_date"]) : null,
        day: String(r["day"]),
      })),
      bestMonth: months.rows[0] ? String(months.rows[0]["m"]) : null,
    };
  }
}

async function seoIntro(origin: string, dest: string): Promise<string | null> {
  try {
    const rs = await db().execute({
      sql: `SELECT intro_es FROM seo_pages WHERE origin = ? AND dest = ?`,
      args: [origin, dest],
    });
    const v = rs.rows[0]?.["intro_es"];
    return v ? String(v) : null;
  } catch {
    return null;
  }
}

function HistorySvg({ points }: { points: { day: string; price: number }[] }) {
  if (points.length < 2) {
    return (
      <p className="text-[13px] text-vz-ink-soft">
        histórico en construcción — el detector lleva pocos días midiendo
        esta ruta
      </p>
    );
  }
  const w = 640;
  const h = 180;
  const pad = 10;
  const prices = points.map((p) => p.price);
  const lo = Math.min(...prices);
  const hi = Math.max(...prices);
  const span = Math.max(1, hi - lo);
  const xs = points.map(
    (_, i) => pad + ((w - 2 * pad) * i) / (points.length - 1),
  );
  const ys = points.map(
    (p) => h - pad - ((h - 2 * pad) * (p.price - lo)) / span,
  );
  const d = xs.map((x, i) => `${i ? "L" : "M"}${x},${ys[i]}`).join(" ");
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label="Histórico de precios de la ruta"
      className="w-full rounded-lg border border-vz-line bg-white/60"
    >
      <path d={d} fill="none" stroke="#1c1917" strokeWidth={2.5} />
      <circle cx={xs[xs.length - 1]} cy={ys[ys.length - 1]} r={5} fill="#d97706" />
      <text x={pad + 2} y={16} fontSize={12} fill="#6e6860">
        máx {hi} €
      </text>
      <text x={pad + 2} y={h - 6} fontSize={12} fill="#6e6860">
        mín {lo} €
      </text>
    </svg>
  );
}

const MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

function monthName(ym: string): string {
  const m = Number(ym.slice(5, 7));
  return `${MONTHS_ES[m - 1] ?? ym} de ${ym.slice(0, 4)}`;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ origin: string; dest: string }>;
}): Promise<Metadata> {
  const { origin, dest } = await params;
  const hub = HUBS[origin.toLowerCase()];
  if (!hub) return {};
  const destIata = dest.toUpperCase();
  const base: Metadata = {
    title: `Vuelos baratos ${hub.city} – ${destName(destIata)} | Vuelazo`,
    description:
      `Precio normal y chollos verificados de ${hub.city} a ` +
      `${destName(destIata)}, medidos a diario con datos propios.`,
  };
  try {
    const stats = await routeStats(hub.iata, destIata);
    const mature = stats.verifiedCount >= MIN_OBSERVATIONS;
    // The D6 quality gate: thin routes exist but are NOT indexed.
    return mature ? base : { ...base, robots: { index: false, follow: true } };
  } catch {
    // DB hiccup: don't emit noindex from an infrastructure failure —
    // keep whatever the crawler last saw.
    return base;
  }
}

export default async function RoutePage({
  params,
}: {
  params: Promise<{ origin: string; dest: string }>;
}) {
  const { origin, dest } = await params;
  const hub = HUBS[origin.toLowerCase()];
  const destIata = dest.toUpperCase();
  if (!hub || !/^[A-Z]{3}$/.test(destIata)) notFound();
  const [stats, intro] = await Promise.all([
    routeStats(hub.iata, destIata),
    seoIntro(hub.iata, destIata),
  ]);
  if (stats.history.length === 0) notFound(); // nothing measured: no page
  const name = destName(destIata);
  const normal = stats.baselineMedian ?? stats.cachedMedian;
  const mature = stats.verifiedCount >= MIN_OBSERVATIONS;

  return (
    <div className="space-y-10">
      <section>
        <p className="text-[12px] uppercase tracking-[2px] text-vz-ink-soft">
          {hub.city} ({hub.iata}) → {name} ({destIata})
        </p>
        <h1 className="mt-1 font-vz-display text-3xl font-semibold sm:text-4xl">
          Vuelos baratos de {hub.city} a {name}
        </h1>
        {intro ? (
          <p className="mt-4 max-w-2xl text-[15px] leading-relaxed text-vz-ink">
            {intro}
          </p>
        ) : (
          <p className="mt-4 max-w-2xl text-[15px] leading-relaxed text-vz-ink-soft">
            Medimos esta ruta todos los días con datos propios — nada de
            precios patrocinados. Aquí tienes su histórico, su precio
            normal y los mejores precios recientes.
          </p>
        )}
      </section>

      <section className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-vz-line bg-white/60 p-4">
          <p className="text-[11px] uppercase tracking-[2px] text-vz-ink-soft">
            precio normal
          </p>
          <p className="mt-1 font-vz-display text-3xl font-semibold">
            {normal != null ? `${normal} €` : "—"}
          </p>
          <p className="mt-1 text-[11px] text-vz-ink-soft">
            {mature
              ? `mediana de ${stats.verifiedCount} tarifas verificadas (60 días)`
              : "mediana de mínimos cacheados (60 días) — aún provisional"}
          </p>
        </div>
        <div className="rounded-lg border border-vz-line bg-white/60 p-4">
          <p className="text-[11px] uppercase tracking-[2px] text-vz-ink-soft">
            mejor precio reciente
          </p>
          <p className="mt-1 font-vz-display text-3xl font-semibold text-vz-amber">
            {stats.best[0] ? `${stats.best[0].price} €` : "—"}
          </p>
          <p className="mt-1 text-[11px] text-vz-ink-soft">
            mínimo cacheado, últimos 14 días
          </p>
        </div>
        <div className="rounded-lg border border-vz-line bg-white/60 p-4">
          <p className="text-[11px] uppercase tracking-[2px] text-vz-ink-soft">
            cuándo suele salir mejor
          </p>
          <p className="mt-1 font-vz-display text-xl font-semibold">
            {stats.bestMonth ? monthName(stats.bestMonth) : "—"}
          </p>
          <p className="mt-1 text-[11px] text-vz-ink-soft">
            el mes con mínimos más bajos en nuestra ventana de datos
          </p>
        </div>
      </section>

      <section>
        <h2 className="font-vz-display text-2xl font-semibold">
          El precio, demostrado
        </h2>
        <p className="mb-3 mt-1 text-[12px] text-vz-ink-soft">
          mínimo diario observado — así distinguimos un chollo de un
          martes normal
        </p>
        <HistorySvg points={stats.history} />
      </section>

      {stats.best.length > 0 && (
        <section>
          <h2 className="font-vz-display text-2xl font-semibold">
            Mejores precios recientes
          </h2>
          <ul className="mt-3 divide-y divide-vz-line rounded-lg border border-vz-line bg-white/60">
            {stats.best.map((b, i) => (
              <li
                key={i}
                className="flex flex-wrap items-baseline gap-x-4 px-4 py-3 text-[14px]"
              >
                <span className="font-semibold text-vz-amber">{b.price} €</span>
                <span className="text-vz-ink">
                  salida {b.dep}
                  {b.ret ? ` · vuelta ${b.ret}` : " · solo ida"}
                </span>
                <span className="ml-auto text-[12px] text-vz-ink-soft">
                  visto el {b.day}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-vz-ink-soft">
            precios cacheados orientativos; los avisos de Vuelazo se
            verifican en vivo antes de enviarse.
          </p>
        </section>
      )}

      <section className="rounded-lg bg-vz-paper2 p-6">
        <h2 className="font-vz-display text-xl font-semibold">
          Te avisamos cuando {hub.city} → {name} caiga de verdad
        </h2>
        <p className="mb-4 mt-1 text-[14px] text-vz-ink-soft">
          Gratis, cada domingo — o al instante con el{" "}
          <Link href="/unete" className="font-semibold text-vz-amber-deep hover:underline">
            pase anual
          </Link>
          .
        </p>
        <SubscribeForm compact />
      </section>
    </div>
  );
}
