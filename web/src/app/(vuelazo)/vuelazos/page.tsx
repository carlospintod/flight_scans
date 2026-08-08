// Public archive (M4a): 24h-delayed published deals, per-airport filter.

import Link from "next/link";
import { getPublishedDeals } from "@/lib/deals";

// Dynamic by nature (searchParams filter) — no ISR pretense; the page
// reads live Turso per request like /cuenta does.
export const dynamic = "force-dynamic";

const ORIGINS = ["MAD", "BCN", "VLC", "ALC"] as const;

export default async function VuelazosPage({
  searchParams,
}: {
  searchParams: Promise<{ desde?: string }>;
}) {
  const { desde } = await searchParams;
  const origin = ORIGINS.includes((desde ?? "").toUpperCase() as never)
    ? (desde ?? "").toUpperCase()
    : undefined;
  const deals = await getPublishedDeals({
    origin,
    delayedHours: 24,
    limit: 60,
  });

  return (
    <div>
      <h1 className="font-vz-display text-3xl font-semibold">
        El archivo de vuelazos
      </h1>
      <p className="mt-2 max-w-2xl text-[15px] text-vz-ink-soft">
        Todo lo publicado, 24 horas después que los miembros. Cada precio
        fue verificado en vivo antes de avisar.
      </p>

      <div className="mt-6 flex flex-wrap gap-2">
        <Link
          href="/vuelazos"
          className={`rounded-full border px-3 py-1 text-[13px] ${
            !origin
              ? "border-vz-amber bg-vz-amber text-vz-paper"
              : "border-vz-line text-vz-ink-soft hover:text-vz-ink"
          }`}
        >
          todos
        </Link>
        {ORIGINS.map((o) => (
          <Link
            key={o}
            href={`/vuelazos?desde=${o}`}
            className={`rounded-full border px-3 py-1 text-[13px] ${
              origin === o
                ? "border-vz-amber bg-vz-amber text-vz-paper"
                : "border-vz-line text-vz-ink-soft hover:text-vz-ink"
            }`}
          >
            {o}
          </Link>
        ))}
      </div>

      {deals.length === 0 ? (
        <p className="mt-10 text-[15px] text-vz-ink-soft">
          Aún nada aquí{origin ? ` desde ${origin}` : ""} — el detector
          publica solo lo que pasa el listón. Vuelve pronto.
        </p>
      ) : (
        <div className="mt-8 space-y-6">
          {deals.map((d) => (
            <article
              key={d.id}
              className="rounded-lg border border-vz-line bg-white/60 p-5"
            >
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <h2 className="font-vz-display text-2xl font-semibold">
                  {d.origin} → {d.dest}
                </h2>
                <span className="text-2xl font-semibold text-vz-amber">
                  {d.price} €
                </span>
                {d.pctBelow != null && (
                  <span className="text-[13px] font-semibold text-vz-ink">
                    −{Math.round(d.pctBelow)}% vs. normal
                  </span>
                )}
                <span className="ml-auto text-[12px] text-vz-ink-soft">
                  {d.publishedAt?.slice(0, 10)}
                </span>
              </div>
              {d.baselineMedian != null && (
                <p className="mt-1 text-[13px] text-vz-ink-soft">
                  precio normal de la ruta: {d.baselineMedian} € — demostrado
                  con histórico, no estimado.
                </p>
              )}
              {d.draftEs && (
                <p className="mt-3 max-w-3xl whitespace-pre-wrap text-[14px] leading-relaxed text-vz-ink">
                  {d.draftEs}
                </p>
              )}
            </article>
          ))}
        </div>
      )}

      <p className="mt-12 border-t border-vz-line pt-6 text-[14px] text-vz-ink-soft">
        ¿Cansado de llegar 24 horas tarde?{" "}
        <Link href="/unete" className="font-semibold text-vz-amber-deep hover:underline">
          El pase anual te los da al instante →
        </Link>
      </p>
    </div>
  );
}
