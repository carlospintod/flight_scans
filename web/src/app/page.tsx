import { AlertsFeed } from "@/components/AlertsFeed";
import { AlternativesTable } from "@/components/AlternativesTable";
import { PriceHero } from "@/components/PriceHero";
import { SectionHeading } from "@/components/Section";
import { StaleBadge } from "@/components/StaleBadge";
import { fmtDate } from "@/lib/format";
import {
  getLatestScanRun,
  getRecentAlerts,
  getRouteWindow,
  getTopAlternatives,
} from "@/lib/queries";

// ISR safety net; scans additionally ping /api/revalidate so the page is
// fresh within seconds of new data. Data changes ~3x/week — never query
// per-request here (Turso read budget).
export const revalidate = 21600;

export default async function RadarPage() {
  const w = await getRouteWindow();
  const [run, alternatives, alerts] = await Promise.all([
    getLatestScanRun(w.routeId),
    getTopAlternatives(w, 10),
    getRecentAlerts(w.routeId, 8),
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
        <SectionHeading>Price alerts</SectionHeading>
        <AlertsFeed alerts={alerts} />
      </section>
    </div>
  );
}
