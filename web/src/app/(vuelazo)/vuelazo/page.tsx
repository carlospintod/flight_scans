// Vuelazo landing (M4a). src/proxy.ts rewrites "/" -> this page for the
// vuelazo.es host (already wired); on every other host it lives at
// /vuelazo and the tracker keeps "/".

import Link from "next/link";
import { db } from "@/lib/db";
import SubscribeForm from "@/components/member/SubscribeForm";
import { getPublishedDeals } from "@/lib/deals";

export const revalidate = 3600;

async function totalVerifiedSavings(): Promise<number> {
  try {
    const rs = await db().execute(
      "SELECT COALESCE(SUM(abs_saving), 0) AS s FROM deals WHERE status = 'published'",
    );
    return Number(rs.rows[0]?.["s"] ?? 0);
  } catch {
    return 0;
  }
}

export default async function VuelazoLanding() {
  const [savings, deals] = await Promise.all([
    totalVerifiedSavings(),
    getPublishedDeals({ delayedHours: 24, limit: 3 }),
  ]);

  return (
    <div className="space-y-16">
      {/* hero */}
      <section className="pt-6">
        <h1 className="max-w-3xl font-vz-display text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
          El precio normal, demostrado —{" "}
          <span className="text-vz-amber">y el chollo, a tiempo.</span>
        </h1>
        <p className="mt-5 max-w-2xl text-[17px] leading-relaxed text-vz-ink-soft">
          Vigilamos los vuelos desde <strong className="text-vz-ink">València,
          Alacant, Madrid y Barcelona</strong> todos los días. Cuando un
          precio cae de verdad — verificado en vivo, con su histórico
          delante — te avisamos. Sin humo, sin «¡INCREÍBLE!», sin prisas
          falsas.
        </p>
        <div className="mt-8">
          <SubscribeForm />
        </div>
        {savings > 0 && (
          <p className="mt-6 text-[13px] text-vz-ink-soft">
            Ahorro verificado acumulado:{" "}
            <span className="font-semibold text-vz-ink">{savings} €</span>{" "}
            frente al precio normal de cada ruta.
          </p>
        )}
      </section>

      {/* latest delayed deals */}
      <section>
        <h2 className="font-vz-display text-2xl font-semibold">
          Últimos vuelazos
          <span className="ml-2 align-middle text-[12px] font-normal text-vz-ink-soft">
            (los miembros los vieron 24 h antes)
          </span>
        </h2>
        {deals.length === 0 ? (
          <p className="mt-4 text-[15px] text-vz-ink-soft">
            El detector está calentando motores — los primeros chollos
            aparecerán aquí esta semana.
          </p>
        ) : (
          <div className="mt-5 grid gap-4 sm:grid-cols-3">
            {deals.map((d) => (
              <article
                key={d.id}
                className="rounded-lg border border-vz-line bg-white/60 p-4"
              >
                <p className="font-vz-display text-xl font-semibold">
                  {d.origin} → {d.dest}
                </p>
                <p className="mt-1 text-2xl font-semibold text-vz-amber">
                  {d.price} €
                </p>
                {d.baselineMedian != null && (
                  <p className="mt-1 text-[12px] text-vz-ink-soft">
                    precio normal {d.baselineMedian} €
                    {d.pctBelow != null && <> · −{Math.round(d.pctBelow)}%</>}
                  </p>
                )}
              </article>
            ))}
          </div>
        )}
        <Link
          href="/vuelazos"
          className="mt-4 inline-block text-[14px] font-semibold text-vz-amber-deep hover:underline"
        >
          Ver todos los chollos →
        </Link>
      </section>

      {/* how it works */}
      <section className="grid gap-6 sm:grid-cols-3">
        {[
          [
            "1 · Medimos",
            "Cientos de tarifas cacheadas al día por origen. De ahí sale el precio normal de cada ruta — con datos, no con adjetivos.",
          ],
          [
            "2 · Verificamos",
            "Ningún aviso sin confirmación en vivo en Google Flights. Lo que te llega existe y se puede reservar.",
          ],
          [
            "3 · Avisamos",
            "Un humano aprueba cada chollo. Los miembros lo reciben al instante en Telegram y email, solo de sus aeropuertos.",
          ],
        ].map(([t, b]) => (
          <div key={t} className="rounded-lg border border-vz-line p-5">
            <h3 className="font-vz-display text-lg font-semibold">{t}</h3>
            <p className="mt-2 text-[14px] leading-relaxed text-vz-ink-soft">
              {b}
            </p>
          </div>
        ))}
      </section>

      {/* founder story (growth guideline #1) */}
      <section className="rounded-lg bg-vz-paper2 p-6 sm:p-8">
        <h2 className="font-vz-display text-2xl font-semibold">
          Esto empezó con un vuelo a Nairobi
        </h2>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-vz-ink-soft">
          Soy Carlos, analista de datos en Castellón. Quería un vuelo
          barato a Nairobi y no me fiaba de los «chollos» de siempre, así
          que construí un radar que mide el precio normal de una ruta y
          avisa cuando cae de verdad. Funcionó tan bien que lo apunté a
          los aeropuertos de casa. Eso es Vuelazo: el mismo detector,
          para València, Alacant, Madrid y Barcelona — con cada aviso
          demostrado con su gráfica.
        </p>
      </section>

      {/* membership CTA */}
      <section className="border-t border-vz-line pt-10">
        <h2 className="font-vz-display text-2xl font-semibold">
          ¿Los quieres todos, al instante?
        </h2>
        <p className="mt-2 max-w-2xl text-[15px] text-vz-ink-soft">
          El pase anual te da los 5–7 chollos de cada semana en el momento,
          solo de tus aeropuertos. 29 € el primer año (precio fundador),
          sin renovación automática, 14 días de garantía.
        </p>
        <Link
          href="/unete"
          className="mt-5 inline-block rounded-md bg-vz-amber px-6 py-3 text-[16px] font-semibold text-vz-paper hover:bg-vz-amber-deep"
        >
          Hazte miembro →
        </Link>
      </section>
    </div>
  );
}
