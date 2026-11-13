import Link from "next/link";
import { fmtDate } from "@/lib/format";
import type { Alert } from "@/lib/types";

const TYPE_BADGE: Record<string, string> = {
  new_low: "border-good text-good",
  drop: "border-cyan99 text-cyan99",
};

/** What the alert's reference price IS depends on its type (lib/alerts.py):
 *  new_low compares against the trip's previous all-time low, drop
 *  against its trailing median. Saying "usual" for both would misstate
 *  every new_low row. */
function comparison(a: Alert): string {
  const pct = Math.round(a.dropPct);
  return a.alertType === "new_low"
    ? `${pct}% under its previous low of ${a.baselineMedian}`
    : `${pct}% under its usual ${a.baselineMedian}`;
}

export function AlertsFeed({ alerts }: { alerts: Alert[] }) {
  if (alerts.length === 0) {
    return (
      <p className="font-mono text-sm text-text-mid">
        No alerts for trips in this window yet — they fire when a price
        drops below its history.
      </p>
    );
  }
  return (
    <ul className="space-y-1.5">
      {alerts.map((a, i) => (
        <li key={i}>
          <Link
            href={`/i/${a.origin}/${a.destination}/${a.departureDate}/${a.returnDate}`}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-card border border-border bg-bg2 px-3 py-2 font-mono text-[12px] hover:border-border-bright"
          >
            <span
              className={`rounded-card border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${
                TYPE_BADGE[a.alertType] ?? "border-hint text-hint"
              }`}
            >
              {a.alertType === "new_low" ? "new low" : a.alertType}
            </span>
            <span className="font-semibold text-text-bright">
              {a.price} {a.currency}
            </span>
            <span className="text-text">
              {a.origin}→{a.destination} {fmtDate(a.departureDate)}
              {a.returnDate && `–${fmtDate(a.returnDate)}`}
            </span>
            <span className="text-text-mid">{comparison(a)}</span>
            <span className="text-hint">{fmtDate(a.firedAt.slice(0, 10))}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
