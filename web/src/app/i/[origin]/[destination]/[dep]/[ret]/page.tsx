import Link from "next/link";
import { notFound } from "next/navigation";
import { AlertsFeed } from "@/components/AlertsFeed";
import { HistoryChart } from "@/components/HistoryChart";
import { Card, SectionHeading } from "@/components/Section";
import { fmtDateLong, fmtDuration, fmtStops } from "@/lib/format";
import {
  getItineraryAlerts,
  getItineraryDetail,
  getItineraryHistory,
  getRouteWindow,
} from "@/lib/queries";

export const revalidate = 21600;

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const IATA_RE = /^[A-Z]{3}$/;

export default async function ItineraryPage({
  params,
}: {
  params: Promise<{
    origin: string;
    destination: string;
    dep: string;
    ret: string;
  }>;
}) {
  const { origin, destination, dep, ret } = await params;
  if (
    !IATA_RE.test(origin) ||
    !IATA_RE.test(destination) ||
    !DATE_RE.test(dep) ||
    !DATE_RE.test(ret)
  ) {
    notFound();
  }
  const w = await getRouteWindow();
  const [history, detail, alerts] = await Promise.all([
    getItineraryHistory(w.routeId, origin, destination, dep, ret),
    getItineraryDetail(w.routeId, origin, destination, dep, ret),
    getItineraryAlerts(w.routeId, origin, destination, dep, ret),
  ]);
  if (history.length === 0 && detail.length === 0) notFound();

  const stay = Math.round(
    (Date.parse(ret) - Date.parse(dep)) / 86_400_000,
  );

  return (
    <div className="space-y-8">
      <div>
        <Link
          href="/"
          className="font-mono text-[11px] uppercase tracking-wider text-hint hover:text-signature"
        >
          ← radar
        </Link>
        <h1 className="mt-2 font-mono text-2xl text-text-bright">
          {origin} → {destination}
        </h1>
        <p className="mt-1 font-mono text-[13px] text-text-mid">
          out {fmtDateLong(dep)} · back {fmtDateLong(ret)} · {stay} day stay
        </p>
      </div>

      <section>
        <SectionHeading>Price over time</SectionHeading>
        <Card>
          <HistoryChart points={history} currency={w.currency} />
        </Card>
      </section>

      {detail.length > 0 && (
        <section>
          <SectionHeading>
            Verified options · {detail[0].snapshotAt.slice(0, 10)} ·{" "}
            {detail[0].source}
          </SectionHeading>
          <div className="grid gap-3 sm:grid-cols-3">
            {detail.map((d) => (
              <Card key={d.rank}>
                <div className="font-mono text-2xl font-semibold text-text-bright">
                  {d.price}{" "}
                  <span className="text-sm text-hint">{d.currency}</span>
                </div>
                <div className="mt-1 font-mono text-[13px] text-text">
                  {d.carriers}
                </div>
                <div className="mt-1 font-mono text-[11px] text-hint">
                  {fmtStops(d.stops)} · {fmtDuration(d.totalMinutes)}
                  {d.isSelfTransfer && (
                    <span className="ml-1 text-amber">· self-transfer</span>
                  )}
                </div>
              </Card>
            ))}
          </div>
        </section>
      )}

      <section>
        <SectionHeading>Alerts for this itinerary</SectionHeading>
        <AlertsFeed alerts={alerts} />
      </section>
    </div>
  );
}
