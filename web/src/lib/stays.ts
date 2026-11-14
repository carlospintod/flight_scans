/** Stay lengths a round-trip search prices: every `step`-th day from
 *  `min`, plus `max` (30, 37, ..., 86, 90 for 30-90 step 7). Mirrors
 *  lib/config.StayPreferences.sampled() in Python; the drift guard
 *  web/scripts/check-stays.mjs re-computes Python's fixture through this
 *  file, so the two can never disagree silently. */
export function sampledStays(min: number, max: number, step: number): number[] {
  const lo = Math.max(1, min);
  if (max < lo) return [];
  const s = Math.max(1, step);
  const out: number[] = [];
  for (let d = lo; d <= max; d += s) out.push(d);
  if (out[out.length - 1] !== max) out.push(max);
  return out;
}
