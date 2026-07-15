import Link from "next/link";
import {
  fmtDate,
  fmtStops,
  isStale,
  providerLabel,
  seenLabel,
  verifyUrl,
} from "@/lib/format";
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
              <th className="py-2 pr-4">Airline</th>
              <th className="py-2 pr-4">Stops</th>
              <th className="py-2 pr-4">Via</th>
              <th className="py-2 pr-4">Seen</th>
              <th className="py-2">Check</th>
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
                  {r.isSelfTransfer && (
                    <span className="ml-1 text-[10px] text-amber">
                      self-transfer
                    </span>
                  )}
                </td>
                <td className="py-2 pr-4">{fmtStops(r.stops)}</td>
                <td className="py-2 pr-4 text-hint">
                  {providerLabel(r.source)}
                  {r.seller && (
                    <span className="block text-[11px] text-signature">
                      via {r.seller}
                    </span>
                  )}
                </td>
                <td
                  className={`py-2 pr-4 ${
                    isStale(r.snapshotAt) ? "text-amber" : "text-hint"
                  }`}
                >
                  {seenLabel(r.snapshotAt).replace("seen ", "")}
                </td>
                <td className="py-2">
                  <a
                    href={verifyUrl(r)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-hint hover:text-signature"
                    title="Check this fare live on Kayak"
                  >
                    live ↗
                  </a>
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
                {providerLabel(r.source)}
                {r.seller && (
                  <span className="text-signature"> · via {r.seller}</span>
                )}
              </div>
            </Link>
            <div className="mt-1.5 flex items-center justify-between text-[11px]">
              <span className={isStale(r.snapshotAt) ? "text-amber" : "text-hint"}>
                {seenLabel(r.snapshotAt)}
              </span>
              <a
                href={verifyUrl(r)}
                target="_blank"
                rel="noopener noreferrer"
                className="text-hint hover:text-signature"
              >
                check live ↗
              </a>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function itinHref(r: Itinerary): string {
  return `/i/${r.origin}/${r.destination}/${r.departureDate}/${r.returnDate}`;
}
