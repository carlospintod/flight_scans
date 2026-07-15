import { db } from "./db";
import { predictUpperBounds } from "./predict";
import { RUNS_PER_MONTH } from "./capacity-constants";

export { RUNS_PER_MONTH };

export interface CapacityView {
  // The binding monthly budget. Since Kiwi was retired (2026-07-13) and
  // gf scraping got captcha-walled from CI (2026-07-14), SerpApi is the
  // metered PRIMARY discovery rail: a live date grid + OTA seller check
  // each scan. Aviasales (cached) and Google Flights (free scraper) don't
  // gate; SerpApi is the only monthly pool a search draws down.
  serpapi: {
    available: number | null;      // live remaining minus safety margin
    periodLimit: number | null;
    committedPerScan: number;      // all active searches' reserved serpapi
    committedMonthly: number;
    runsPerMonth: number;
  };
  activeSearches: number;
}

/** Shared-pool capacity: live SerpApi availability vs the monthly load
 *  ALL active searches already reserve (B5: a per-search-only check would
 *  lie). Used by /api/capacity AND the create-search verdict — one
 *  implementation. Discovery/verification are free, so they don't gate. */
export async function capacityView(): Promise<CapacityView> {
  const client = db();
  const pool = await client.execute(
    `SELECT qp.period_limit, qp.safety_margin,
            (SELECT pa.baseline_remaining FROM pool_anchors pa
             WHERE pa.source = 'serpapi' ORDER BY pa.anchor_id DESC LIMIT 1)
            AS anchor_remaining,
            (SELECT COALESCE(SUM(se.units), 0) FROM spend_events se
             WHERE se.source = 'serpapi' AND se.event_id > COALESCE((
                SELECT pa2.last_spend_event_id FROM pool_anchors pa2
                WHERE pa2.source = 'serpapi'
                ORDER BY pa2.anchor_id DESC LIMIT 1), 0)) AS spent_since
     FROM quota_pools qp WHERE qp.source = 'serpapi'`,
  );
  const p = pool.rows[0];
  const available =
    p && p["anchor_remaining"] !== null
      ? Number(p["anchor_remaining"]) - Number(p["spent_since"]) -
        Number(p["safety_margin"])
      : null;

  const searches = await client.execute(
    `SELECT s.search_id, r.config_json FROM searches s
     JOIN routes r ON r.route_id = s.search_id
     WHERE s.status = 'active'`,
  );
  let committedPerScan = 0;
  for (const row of searches.rows) {
    try {
      const cfg = JSON.parse(String(row["config_json"]));
      committedPerScan += predictUpperBounds({
        nOrigins: cfg.route.origins.length,
        nDestinations: cfg.route.destinations.length,
        earliestDeparture: cfg.search_window.earliest_departure,
        latestReturn: cfg.search_window.latest_return,
        minStayDays: cfg.stay_preferences?.min_days ?? 1,
        tripType: cfg.trip_type === "one_way" ? "one_way" : "round_trip",
      }).serpapi;
    } catch {
      /* unparseable config never blocks the meter */
    }
  }
  return {
    serpapi: {
      available,
      periodLimit: p ? Number(p["period_limit"]) : null,
      committedPerScan,
      committedMonthly: committedPerScan * RUNS_PER_MONTH,
      runsPerMonth: RUNS_PER_MONTH,
    },
    activeSearches: searches.rows.length,
  };
}
