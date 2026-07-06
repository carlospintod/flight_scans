/** Small display helpers shared by server and client components. */

export function fmtDate(iso: string): string {
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00Z" : ""));
  return d.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

export function fmtDateLong(iso: string): string {
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00Z" : ""));
  return d.toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function fmtDuration(minutes: number | null): string {
  if (minutes === null) return "—";
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function fmtStops(stops: number | null): string {
  if (stops === null) return "—";
  if (stops === 0) return "nonstop";
  return `${stops} stop${stops > 1 ? "s" : ""}`;
}

/** Age of a timestamp in whole days (UTC). */
export function ageDays(iso: string): number {
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
}

export type Freshness = "fresh" | "aging" | "stale";

/** Stale-data ladder for the badge: amber >4d, red >8d (plan Phase 2). */
export function freshness(iso: string | null): Freshness {
  if (!iso) return "stale";
  const d = ageDays(iso);
  if (d > 8) return "stale";
  if (d > 4) return "aging";
  return "fresh";
}
