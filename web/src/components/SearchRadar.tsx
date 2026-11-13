import { AlertsFeed } from "@/components/AlertsFeed";
import { AlternativesTable } from "@/components/AlternativesTable";
import { CarrierBar } from "@/components/CarrierBar";
import { Heatmap } from "@/components/Heatmap";
import { PriceCurve } from "@/components/PriceCurve";
import { PriceHero } from "@/components/PriceHero";
import { Card, SectionHeading } from "@/components/Section";
import { StaleBadge } from "@/components/StaleBadge";
import { fmtDate } from "@/lib/format";
import {
  FRESH_WINDOW_DAYS,
  MAX_DISPLAY_PRICE,
  getCarrierMix,
  getHeatmapGrid,
  getLatestScanRun,
  getOneWayCurve,
  getRecentAlerts,
  getTopAlternatives,
} from "@/lib/queries";
import type { RouteWindow } from "@/lib/types";
import { datePairsPerOrigin, lastDeparture } from "@/lib/window";

const n = (x: number) => x.toLocaleString("en-GB");

/** The full radar view for ONE search — used by the public demo (/)
 *  and every per-search results page (/s/[slug]). One codepath, with a
 *  round-trip (heatmap) vs one-way (price curve) branch on the grid.
 *
 *  Every section says what it answers (SectionHeading hint), and the
 *  header says what is being searched and how much of it has actually
 *  been priced — without that, a sparse grid reads as a broken one. */
export async function SearchRadar({ w }: { w: RouteWindow }) {
  const oneWay = w.tripType === "one_way";
  const lastDep = lastDeparture(w);
  const [run, alternatives, alerts, carriers, rawGrids, curve] = await Promise.all([
    getLatestScanRun(w.routeId),
    getTopAlternatives(w, 10),
    getRecentAlerts(w, 8),
    getCarrierMix(w),
    oneWay ? Promise.resolve([]) : Promise.all(w.origins.map((o) => getHeatmapGrid(w, o))),
    oneWay ? getOneWayCurve(w) : Promise.resolve([]),
  ]);
  const best = alternatives[0] ?? null;

  // The grid query bounds departures from below only; the departure cap
  // (latest_departure / latestReturn - minStay) is applied here so the
  // grid and the coverage count agree on what "the window" is.
  const grids = rawGrids.map((cells) => cells.filter((c) => c.departureDate <= lastDep));
  const pairsPerOrigin = datePairsPerOrigin(w);
  const pricedPerOrigin = grids.map((g) => g.length);
  const priced = pricedPerOrigin.reduce((a, b) => a + b, 0);
  const possible = pairsPerOrigin * w.origins.length;

  return (
    <div className="space-y-10">
      <section>
        <div className="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="space-y-1">
            <h1 className="font-mono text-sm font-semibold text-text-bright">
              {w.origins.join("/")} → {w.destinations.join("/")}
            </h1>
            <p className="font-mono text-[12px] text-text-mid">
              {oneWay
                ? `one-way · leaving ${fmtDate(w.earliestDeparture)} – ${fmtDate(lastDep)}`
                : `leaving ${fmtDate(w.earliestDeparture)} – ${fmtDate(lastDep)} · ` +
                  `staying ${w.minStay}–${w.maxStay} days · back by ${fmtDate(w.latestReturn)}`}
            </p>
            {!oneWay && (
              <p className="font-mono text-[12px] text-text-mid">
                {n(priced)} of {n(possible)} date pairs priced in the last{" "}
                {FRESH_WINDOW_DAYS} days
              </p>
            )}
          </div>
          <StaleBadge run={run} />
        </div>
        <PriceHero
          best={best}
          window={w}
          pricedCount={oneWay ? curve.length : priced}
          ceiling={MAX_DISPLAY_PRICE}
        />
      </section>

      {!oneWay && (
        <section>
          <SectionHeading hint="Best trips found — one row per departure day, cheapest first.">
            Cheapest per departure day
          </SectionHeading>
          <AlternativesTable rows={alternatives} />
        </section>
      )}

      {oneWay ? (
        <section>
          <SectionHeading>Cheapest one-way · by departure day</SectionHeading>
          <Card>
            <PriceCurve points={curve} currency={w.currency} />
          </Card>
        </section>
      ) : (
        <section>
          <SectionHeading hint="When to leave × how long to stay. Each square is one trip; empty squares have not been priced yet.">
            Price grid · departure × stay length
          </SectionHeading>
          <div className="space-y-4">
            {w.origins.map((origin, i) => (
              <Card key={origin}>
                <div className="mb-1 flex flex-wrap items-baseline justify-between gap-x-4 font-mono text-[11px] tracking-wider">
                  <span className="uppercase text-text-mid">
                    {origin} → {w.destinations.join("/")}
                  </span>
                  <span className="text-hint">
                    {n(pricedPerOrigin[i] ?? 0)} of {n(pairsPerOrigin)} priced
                  </span>
                </div>
                <Heatmap
                  cells={grids[i] ?? []}
                  currency={w.currency}
                  window={w}
                  ceiling={MAX_DISPLAY_PRICE}
                />
              </Card>
            ))}
          </div>
        </section>
      )}

      <section>
        <SectionHeading hint="Times a trip's price fell below its previous low, or well below its usual price.">
          Price alerts
        </SectionHeading>
        <AlertsFeed alerts={alerts} />
      </section>

      <section>
        <SectionHeading hint="Which airline was cheapest, counted across priced trips.">
          Who has the cheap fares
        </SectionHeading>
        <Card>
          <CarrierBar carriers={carriers} />
        </Card>
      </section>
    </div>
  );
}
