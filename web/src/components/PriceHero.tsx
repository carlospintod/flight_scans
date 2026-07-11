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
      <div className="rounded-card border border-border bg-bg2 p-6 font-mono text-sm text-text-mid">
        No prices collected inside the current window yet. The next scan
        fills this in.
      </div>
    );
  }
  const stale = isStale(best.snapshotAt);
  return (
    <div
      className={`rounded-card border bg-bg2 p-6 ${
        stale ? "border-amber/40" : "border-border"
      }`}
    >
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-[11px] uppercase tracking-[2px] text-hint">
          Cheapest observed{w.tripType === "one_way"
            ? " · one-way"
            : ` · ${w.minStay}–${w.maxStay} day stay`}
        </span>
        <span
          className={`font-mono text-[11px] tracking-wider ${
            stale ? "text-amber" : "text-text-mid"
          }`}
        >
          {seenLabel(best.snapshotAt)}
          {stale && " · re-check before booking"}
        </span>
      </div>
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <span className="font-mono text-5xl font-semibold text-good [text-shadow:0_0_18px_rgb(166_227_161/0.45)]">
          {best.price}
          <span className="ml-2 text-2xl text-good">
            {best.currency}
          </span>
        </span>
        <span className="font-mono text-xl text-text-bright">
          {best.origin} → {best.destination}
        </span>
      </div>
      <div className="mt-3 grid gap-x-8 gap-y-1 font-mono text-[13px] text-text sm:grid-cols-2">
        <div>
          <span className="text-hint">
            {w.tripType === "one_way" ? "fly " : "out  "}
          </span>
          {fmtDateLong(best.departureDate)}
        </div>
        {w.tripType !== "one_way" && (
          <>
            <div>
              <span className="text-hint">back&nbsp;</span>
              {fmtDateLong(best.returnDate)}
            </div>
            <div>
              <span className="text-hint">stay&nbsp;</span>
              {best.stayDays} days
            </div>
          </>
        )}
        <div>
          <span className="text-hint">via&nbsp;&nbsp;</span>
          {best.topCarrier ?? "—"}
          {best.stops !== null && ` · ${fmtStops(best.stops)}`}
          {best.totalMinutes !== null && ` · ${fmtDuration(best.totalMinutes)}`}
        </div>
      </div>
      <div className="mt-3 font-mono text-[11px] text-hint">
        source: {best.source}
        {best.isSelfTransfer && (
          <span className="ml-2 text-amber">
            self-transfer itinerary · separate tickets
          </span>
        )}
      </div>
    </div>
  );
}
