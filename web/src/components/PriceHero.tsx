import {
  fmtDateLong,
  fmtDuration,
  fmtStops,
  isStale,
  seenLabel,
} from "@/lib/format";
import type { Itinerary, RouteWindow } from "@/lib/types";

export function PriceHero({
  best,
  window: w,
}: {
  best: Itinerary | null;
  window: RouteWindow;
}) {
  if (!best) {
    return (
      <div className="rounded-card border border-line bg-bg-2 p-6 font-mono text-sm text-fg-mid">
        No prices collected inside the current window yet — the next scan
        fills this in.
      </div>
    );
  }
  const stale = isStale(best.snapshotAt);
  return (
    <div
      className={`rounded-card border bg-bg-2 p-6 ${
        stale ? "border-amber/40" : "border-line"
      }`}
    >
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-[11px] uppercase tracking-[2px] text-fg-dim">
          Cheapest observed · {w.minStay}–{w.maxStay} day stay
        </span>
        <span
          className={`font-mono text-[11px] tracking-wider ${
            stale ? "text-amber" : "text-fg-mid"
          }`}
        >
          {seenLabel(best.snapshotAt)}
          {stale && " · re-check before booking"}
        </span>
      </div>
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <span className="font-mono text-5xl font-semibold text-matrix [text-shadow:0_0_18px_rgb(0_255_65/0.35)]">
          {best.price}
          <span className="ml-2 text-2xl text-matrix-dim">
            {best.currency}
          </span>
        </span>
        <span className="font-mono text-xl text-fg-bright">
          {best.origin} → {best.destination}
        </span>
      </div>
      <div className="mt-3 grid gap-x-8 gap-y-1 font-mono text-[13px] text-fg sm:grid-cols-2">
        <div>
          <span className="text-fg-dim">out&nbsp;&nbsp;</span>
          {fmtDateLong(best.departureDate)}
        </div>
        <div>
          <span className="text-fg-dim">back&nbsp;</span>
          {fmtDateLong(best.returnDate)}
        </div>
        <div>
          <span className="text-fg-dim">stay&nbsp;</span>
          {best.stayDays} days
        </div>
        <div>
          <span className="text-fg-dim">via&nbsp;&nbsp;</span>
          {best.topCarrier ?? "—"}
          {best.stops !== null && ` · ${fmtStops(best.stops)}`}
          {best.totalMinutes !== null && ` · ${fmtDuration(best.totalMinutes)}`}
        </div>
      </div>
      <div className="mt-3 font-mono text-[11px] text-fg-dim">
        source: {best.source}
        {best.isSelfTransfer && (
          <span className="ml-2 text-amber">
            self-transfer itinerary — separate tickets
          </span>
        )}
      </div>
    </div>
  );
}
