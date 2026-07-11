import Link from "next/link";
import { fmtDate, fmtStops, isStale, seenLabel } from "@/lib/format";
import type { Itinerary } from "@/lib/types";

/** Cheapest itinerary per departure day (the collapse mirrors
 *  ui/_common.top_alternatives). Table on >=sm, card rows below. */
export function AlternativesTable({ rows }: { rows: Itinerary[] }) {
  if (rows.length === 0) {
    return (
      <p className="font-mono text-sm text-text-mid">
        Nothing inside the window yet.
      </p>
    );
  }
  return (
    <>
      {/* Desktop table */}
      <div className="hidden overflow-x-auto sm:block">
        <table className="w-full border-collapse font-mono text-[13px]">
          <thead>
            <tr className="border-b border-border-bright text-left text-[11px] uppercase tracking-wider text-hint">
              <th className="py-2 pr-4">Price</th>
              <th className="py-2 pr-4">From</th>
              <th className="py-2 pr-4">Depart</th>
              <th className="py-2 pr-4">Return</th>
              <th className="py-2 pr-4">Stay</th>
              <th className="py-2 pr-4">Carrier</th>
              <th className="py-2 pr-4">Stops</th>
              <th className="py-2 pr-4">Src</th>
              <th className="py-2">Seen</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={`${r.origin}${r.departureDate}${r.returnDate}`}
                className={`border-b border-border hover:bg-bg3 ${
                  isStale(r.snapshotAt) ? "opacity-55" : ""
                }`}
              >
                <td className="py-2 pr-4 font-semibold text-text-bright">
                  <Link
                    href={itinHref(r)}
                    className="text-good hover:text-soft-green"
                  >
                    {r.price} {r.currency}
                  </Link>
                </td>
                <td className="py-2 pr-4">{r.origin}</td>
                <td className="py-2 pr-4">{fmtDate(r.departureDate)}</td>
                <td className="py-2 pr-4">{fmtDate(r.returnDate)}</td>
                <td className="py-2 pr-4">{r.stayDays}d</td>
                <td className="max-w-[200px] truncate py-2 pr-4">
                  {r.topCarrier ?? "—"}
                </td>
                <td className="py-2 pr-4">{fmtStops(r.stops)}</td>
                <td className="py-2 pr-4 text-hint">{r.source}</td>
                <td
                  className={`py-2 ${
                    isStale(r.snapshotAt) ? "text-amber" : "text-hint"
                  }`}
                >
                  {seenLabel(r.snapshotAt).replace("seen ", "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Mobile cards */}
      <ul className="space-y-2 sm:hidden">
        {rows.map((r) => (
          <li
            key={`${r.origin}${r.departureDate}${r.returnDate}`}
            className={`rounded-card border border-border bg-bg2 p-3 font-mono text-[13px] ${
              isStale(r.snapshotAt) ? "opacity-55" : ""
            }`}
          >
            <Link href={itinHref(r)} className="block">
              <div className="flex items-baseline justify-between">
                <span className="text-lg font-semibold text-good">
                  {r.price} {r.currency}
                </span>
                <span className="text-text-mid">{r.origin} → {r.destination}</span>
              </div>
              <div className="mt-1 text-text">
                {fmtDate(r.departureDate)} → {fmtDate(r.returnDate)} ·{" "}
                {r.stayDays}d
              </div>
              <div className="mt-0.5 text-[11px] text-hint">
                {r.topCarrier ?? "carrier unknown"} · {fmtStops(r.stops)} ·{" "}
                {r.source} ·{" "}
                <span className={isStale(r.snapshotAt) ? "text-amber" : ""}>
                  {seenLabel(r.snapshotAt)}
                </span>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}

function itinHref(r: Itinerary): string {
  return `/i/${r.origin}/${r.destination}/${r.departureDate}/${r.returnDate}`;
}
