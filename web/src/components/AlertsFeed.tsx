import Link from "next/link";
import { fmtDate } from "@/lib/format";
import type { Alert } from "@/lib/types";

const TYPE_BADGE: Record<string, string> = {
  new_low: "border-matrix-dim text-matrix",
  drop: "border-cyan text-cyan",
};

export function AlertsFeed({ alerts }: { alerts: Alert[] }) {
  if (alerts.length === 0) {
    return (
      <p className="font-mono text-sm text-fg-mid">
        No alerts yet — they fire when a price drops below its history.
      </p>
    );
  }
  return (
    <ul className="space-y-1.5">
      {alerts.map((a, i) => (
        <li key={i}>
          <Link
            href={`/i/${a.origin}/${a.destination}/${a.departureDate}/${a.returnDate}`}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-card border border-line bg-bg-2 px-3 py-2 font-mono text-[12px] hover:border-line-bright"
          >
            <span
              className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${
                TYPE_BADGE[a.alertType] ?? "border-fg-dim text-fg-dim"
              }`}
            >
              {a.alertType === "new_low" ? "new low" : a.alertType}
            </span>
            <span className="font-semibold text-fg-bright">
              {a.price} {a.currency}
            </span>
            <span className="text-fg">
              {a.origin}→{a.destination} {fmtDate(a.departureDate)}–
              {fmtDate(a.returnDate)}
            </span>
            <span className="text-fg-dim">
              was {a.baselineMedian} · {a.firedAt.slice(0, 10)}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
