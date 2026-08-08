// Airport hub pages (M4a): /vuelos-baratos/valencia etc. Data-derived:
// today's cheapest cached fares + the origin's published deals.

import Link from "next/link";
import { notFound } from "next/navigation";
import { db } from "@/lib/db";
import SubscribeForm from "@/components/member/SubscribeForm";
import { getPublishedDeals } from "@/lib/deals";
import { HUBS, destName } from "@/lib/hubs";

export const revalidate = 21600;

export function generateStaticParams() {
  return Object.keys(HUBS).map((origin) => ({ origin }));
}

type CheapRow = { dest: string; price: number };

async function cheapestNow(iata: string): Promise<CheapRow[]> {
  try {
    const since = new Date(Date.now() - 7 * 864e5)
      .toISOString()
      .slice(0, 10);
    const rs = await db().execute({
      sql: `SELECT dest, MIN(price) AS price FROM fare_observations
            WHERE origin = ? AND observed_at >= ?
            GROUP BY dest ORDER BY price LIMIT 12`,
      args: [iata, since],
    });
    return rs.rows.map((r) => ({
      dest: String(r["dest"]),
      price: Number(r["price"]),
    }));
  } catch {
    return [];
  }
}

export default async function HubPage({
  params,
}: {
  params: Promise<{ origin: string }>;
}) {
  const { origin } = await params;
  const hub = HUBS[origin.toLowerCase()];
  if (!hub) notFound();

  const [cheap, deals] = await Promise.all([
    cheapestNow(hub.iata),
    getPublishedDeals({ origin: hub.iata, delayedHours: 24, limit: 10 }),
  ]);

  return (
    <div className="space-y-12">
      <section>
        <h1 className="font-vz-display text-3xl font-semibold sm:text-4xl">
          Vuelos baratos {hub.desde}
        </h1>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-vz-ink-soft">
          Medimos los precios desde {hub.city} ({hub.iata}) todos los
          días. Esto no es una lista de ofertas patrocinadas: es lo que
          la caché de tarifas dice hoy, y debajo, los chollos que
          verificamos en vivo antes de avisar.
        </p>
      </section>

      {cheap.length > 0 && (
        <section>
          <h2 className="font-vz-display text-2xl font-semibold">
            Lo más barato ahora mismo
          </h2>
          <p className="mt-1 text-[12px] text-vz-ink-soft">
            mínimos cacheados de los últimos 7 días — orientativos hasta
            verificar en vivo
          </p>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
            {cheap.map((c) => (
              <div
                key={c.dest}
                className="rounded-lg border border-vz-line bg-white/60 px-4 py-3"
              >
                <p className="font-vz-display text-lg font-semibold">
                  {destName(c.dest)}
                </p>
                <p className="text-[15px] font-semibold text-vz-amber">
                  desde {c.price} €
                  <span className="ml-1.5 text-[11px] font-normal text-vz-ink-soft">
                    {c.dest}
                  </span>
                </p>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2 className="font-vz-display text-2xl font-semibold">
          Vuelazos verificados {hub.desde}
        </h2>
        {deals.length === 0 ? (
          <p className="mt-3 text-[15px] text-vz-ink-soft">
            Todavía ninguno publicado desde {hub.city} — el detector es
            exigente a propósito. Apúntate y te llega el primero.
          </p>
        ) : (
          <ul className="mt-4 space-y-3">
            {deals.map((d) => (
              <li
                key={d.id}
                className="flex flex-wrap items-baseline gap-x-4 rounded-lg border border-vz-line bg-white/60 px-4 py-3"
              >
                <span className="font-vz-display text-lg font-semibold">
                  {d.origin} → {d.dest}
                </span>
                <span className="text-lg font-semibold text-vz-amber">
                  {d.price} €
                </span>
                {d.pctBelow != null && (
                  <span className="text-[13px] text-vz-ink-soft">
                    −{Math.round(d.pctBelow)}% vs. normal
                  </span>
                )}
                <span className="ml-auto text-[12px] text-vz-ink-soft">
                  {d.publishedAt?.slice(0, 10)}
                </span>
              </li>
            ))}
          </ul>
        )}
        <Link
          href={`/vuelazos?desde=${hub.iata}`}
          className="mt-4 inline-block text-[14px] font-semibold text-vz-amber-deep hover:underline"
        >
          Archivo completo {hub.desde} →
        </Link>
      </section>

      <section className="rounded-lg bg-vz-paper2 p-6">
        <h2 className="font-vz-display text-xl font-semibold">
          Los chollos {hub.desde}, en tu bandeja
        </h2>
        <p className="mb-4 mt-1 text-[14px] text-vz-ink-soft">
          Gratis, cada domingo. Los miembros los reciben al instante.
        </p>
        <SubscribeForm compact />
      </section>
    </div>
  );
}
