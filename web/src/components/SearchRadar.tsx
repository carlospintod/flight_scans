import { AlertsFeed } from "@/components/AlertsFeed";
import { AlternativesTable } from "@/components/AlternativesTable";
import { CarrierBar } from "@/components/CarrierBar";
import { Heatmap } from "@/components/Heatmap";
import { PriceHero } from "@/components/PriceHero";
import { Card, SectionHeading } from "@/components/Section";
import { StaleBadge } from "@/components/StaleBadge";
import { fmtDate } from "@/lib/format";
import {
  getCarrierMix,
  getHeatmapGrid,
  getLatestScanRun,
  getRecentAlerts,
  getTopAlternatives,
} from "@/lib/queries";
import type { RouteWindow } from "@/lib/types";

/** The full radar view for ONE search — used by the public demo (/)
 *  and every per-search results page (/s/[slug]). One codepath. */
export async function SearchRadar({ w }: { w: RouteWindow }) {
  const [run, alternatives, alerts, carriers, grids] = await Promise.all([
    getLatestScanRun(w.routeId),
    getTopAlternatives(w, 10),
    getRecentAlerts(w.routeId, 8),
    getCarrierMix(w),
    Promise.all(w.origins.map((o) => getHeatmapGrid(w, o))),
  ]);
  const best = alternatives[0] ?? null;

  return (
    <div className="space-y-10">
      <section>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <h1 className="font-mono text-sm text-fg-mid">
            {w.origins.join("/")} → {w.destinations.join("/")} ·{" "}
            {fmtDate(w.earliestDeparture)} – {fmtDate(w.latestReturn)} ·{" "}
            {w.minStay}–{w.maxStay} day stays
          </h1>
          <StaleBadge run={run} />
        </div>
        <PriceHero best={best} window={w} />
      </section>

      <section>
        <SectionHeading>Cheapest per departure day</SectionHeading>
        <AlternativesTable rows={alternatives} />
      </section>

      <section>
        <SectionHeading>Price grid · departure × stay length</SectionHeading>
        <div className="space-y-4">
          {w.origins.map((origin, i) => (
            <Card key={origin}>
              <div className="mb-1 font-mono text-[11px] uppercase tracking-wider text-fg-mid">
                {origin} → {w.destinations.join("/")}
              </div>
              <Heatmap cells={grids[i] ?? []} currency={w.currency} />
            </Card>
          ))}
        </div>
      </section>

      <section>
        <SectionHeading>Price alerts</SectionHeading>
        <AlertsFeed alerts={alerts} />
      </section>

      <section>
        <SectionHeading>Who has the cheap fares</SectionHeading>
        <Card>
          <CarrierBar carriers={carriers} />
        </Card>
      </section>
    </div>
  );
}
