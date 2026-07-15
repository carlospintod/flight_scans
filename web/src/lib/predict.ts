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
  tripType?: "round_trip" | "one_way";
}

export interface UpperBounds {
  kiwi: number;
  googleflights: number;
  serpapi: number;
  aviasales: number;
}

const KIWI_BAND_DAYS = 21;
const GF_CAP = 25;
// SerpApi is the metered PRIMARY discovery rail (2026-07-14): a fixed
// live date grid + the OTA seller-check reserve. Must match
// lib/planner.py SERPAPI_DISCOVERY_CAP / SERPAPI_OTA_RESERVE.
const SERPAPI_DISCOVERY_CAP = 5;
const SERPAPI_OTA_RESERVE = 2;

export function predictUpperBounds(p: PredictInput): UpperBounds {
  const oneWay = p.tripType === "one_way";
  const earliest = Date.parse(p.earliestDeparture + "T00:00:00Z");
  const latestReturn = Date.parse(p.latestReturn + "T00:00:00Z");
  const latestDep = oneWay
    ? latestReturn
    : latestReturn - p.minStayDays * 86_400_000;
  const windowDays = Math.max(
    0, Math.round((latestDep - earliest) / 86_400_000) + 1);
  const bandsPerPair =
    windowDays === 0 ? 0 : Math.ceil(windowDays / KIWI_BAND_DAYS);
  const pairs = p.nOrigins * p.nDestinations;
  if (oneWay) {
    // Aviasales one-way corroboration: 1 call per pair per window month.
    const d1 = new Date(earliest);
    const d2 = new Date(latestDep);
    const months = windowDays === 0 ? 0 :
      (d2.getUTCFullYear() - d1.getUTCFullYear()) * 12 +
      (d2.getUTCMonth() - d1.getUTCMonth()) + 1;
    return {
      kiwi: bandsPerPair * pairs,
      googleflights: GF_CAP,
      serpapi: SERPAPI_DISCOVERY_CAP + SERPAPI_OTA_RESERVE,
      aviasales: months * pairs,
    };
  }
  return {
    kiwi: bandsPerPair * pairs,
    googleflights: GF_CAP,
    serpapi: SERPAPI_DISCOVERY_CAP + SERPAPI_OTA_RESERVE,
    aviasales: pairs,
  };
}
