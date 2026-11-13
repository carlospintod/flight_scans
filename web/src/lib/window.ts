import type { RouteWindow } from "./types";

/** Pure search-window geometry for the results page: which departure
 *  days and stays the window allows, and how many date pairs that is.
 *  Same departure bound as predictUpperBounds (lib/predict.ts): a round
 *  trip must leave by latestReturn - minStay, and the optional
 *  latest_departure cap tightens that further. */

const DAY_MS = 86_400_000;

function parse(iso: string): number {
  return Date.parse(iso + "T00:00:00Z");
}

function toIso(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** Last departure day the window allows (YYYY-MM-DD). */
export function lastDeparture(w: RouteWindow): string {
  let last =
    w.tripType === "one_way"
      ? parse(w.latestReturn)
      : parse(w.latestReturn) - w.minStay * DAY_MS;
  if (w.latestDeparture) last = Math.min(last, parse(w.latestDeparture));
  return toIso(last);
}

/** Every departure day in the window, ascending. Empty when the window
 *  is impossible (e.g. stays longer than the window). */
export function departureDays(w: RouteWindow): string[] {
  const out: string[] = [];
  const end = parse(lastDeparture(w));
  for (let t = parse(w.earliestDeparture); t <= end; t += DAY_MS) {
    out.push(toIso(t));
  }
  return out;
}

/** Stay lengths the window asks for, ascending (round trip only). */
export function stayLengths(w: RouteWindow): number[] {
  const out: number[] = [];
  for (let s = w.minStay; s <= w.maxStay; s++) out.push(s);
  return out;
}

/** True when a trip leaving `departure` and staying `stay` days is back
 *  by latestReturn. */
export function fitsWindow(
  w: RouteWindow, departure: string, stay: number,
): boolean {
  return parse(departure) + stay * DAY_MS <= parse(w.latestReturn);
}

/** Date pairs (departure × return) ONE origin can fly inside the window;
 *  for one-way, departure days. Destinations are not multiplied in: the
 *  grid and the coverage count both treat an origin's trips to any of
 *  the window's destinations as one pair. */
export function datePairsPerOrigin(w: RouteWindow): number {
  const days = departureDays(w);
  if (w.tripType === "one_way") return days.length;
  let n = 0;
  for (const d of days) {
    for (const s of stayLengths(w)) if (fitsWindow(w, d, s)) n++;
  }
  return n;
}
