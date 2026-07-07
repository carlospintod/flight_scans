/** TS mirror of lib/planner.py predict_upper_bounds — the per-scan
 *  upper bounds the creation form previews. Pure geometry, no I/O.
 *
 *  DO NOT change this formula without changing the Python original in
 *  the same commit: web/scripts/check-estimator.mjs re-computes a
 *  Python-generated fixture and fails CI on any divergence. */

export interface PredictInput {
  nOrigins: number;
  nDestinations: number;
  earliestDeparture: string; // YYYY-MM-DD
  latestReturn: string;
  minStayDays: number;
}

export interface UpperBounds {
  kiwi: number;
  googleflights: number;
  serpapi_contingency: number;
  aviasales: number;
}

const KIWI_BAND_DAYS = 21;
const GF_CAP = 25;
const SERPAPI_CONTINGENCY = 7;

export function predictUpperBounds(p: PredictInput): UpperBounds {
  const earliest = Date.parse(p.earliestDeparture + "T00:00:00Z");
  const latestReturn = Date.parse(p.latestReturn + "T00:00:00Z");
  const latestDep = latestReturn - p.minStayDays * 86_400_000;
  const windowDays = Math.max(
    0, Math.round((latestDep - earliest) / 86_400_000) + 1);
  const bandsPerPair =
    windowDays === 0 ? 0 : Math.ceil(windowDays / KIWI_BAND_DAYS);
  const pairs = p.nOrigins * p.nDestinations;
  return {
    kiwi: bandsPerPair * pairs,
    googleflights: GF_CAP,
    serpapi_contingency: Math.min(GF_CAP, SERPAPI_CONTINGENCY),
    aviasales: pairs,
  };
}
