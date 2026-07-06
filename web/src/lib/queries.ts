import { db } from "./db";
import type {
  Alert,
  CarrierCount,
  HeatmapCell,
  HistoryPoint,
  Itinerary,
  RouteWindow,
  ScanRun,
} from "./types";

/**
 * Every SQL statement here is a port of a named, tested Python function
 * (lib/db.py or ui/_common.py) — noted per query. Do not invent new SQL;
 * if a page needs a new shape, add it to the Python side first or mirror
 * an existing helper.
 */

const DEFAULT_ROUTE = "spain-nairobi";

/** The "cheapest" views (hero, table, heatmap) only trust observations
 *  this fresh — a month-old price you can't book is not the cheapest
 *  anything. History (the drill-down charts) keeps everything. At the
 *  3x/week cadence the board is normally 0–2 days old; this only bites
 *  when scanning has genuinely lapsed, which the stale badge already
 *  flags. */
const FRESH_WINDOW_DAYS = 21;

function freshCutoff(): string {
  return new Date(Date.now() - FRESH_WINDOW_DAYS * 86_400_000)
    .toISOString()
    .replace(/\.\d{3}Z$/, "Z");
}

/** Effective route config — routes.config_json (canonical shape written
 *  by lib/config.RouteConfig.to_json). Mirrors route_store precedence:
 *  the web app never falls back to YAML (the DB row always exists after
 *  the first scan). */
export async function getRouteWindow(
  routeId: string = DEFAULT_ROUTE,
): Promise<RouteWindow> {
  const rs = await db().execute({
    sql: "SELECT config_json FROM routes WHERE route_id = ?",
    args: [routeId],
  });
  if (rs.rows.length === 0) {
    throw new Error(`route ${routeId} not found in DB`);
  }
  const cfg = JSON.parse(String(rs.rows[0]["config_json"]));
  return {
    routeId,
    origins: cfg.route.origins,
    destinations: cfg.route.destinations,
    earliestDeparture: cfg.search_window.earliest_departure,
    latestReturn: cfg.search_window.latest_return,
    minStay: cfg.stay_preferences.min_days,
    maxStay: cfg.stay_preferences.max_days,
    currency: cfg.currency,
    watchBelowPrice: cfg.followup?.watch_below_price ?? null,
  };
}

/** Mirrors lib/db.latest_scan_run. */
export async function getLatestScanRun(
  routeId: string = DEFAULT_ROUTE,
): Promise<ScanRun | null> {
  const rs = await db().execute({
    sql: `SELECT started_at, finished_at, trigger, sources, rows_stored,
                 alerts_fired, status
          FROM scan_runs WHERE route_id = ?
          ORDER BY started_at DESC LIMIT 1`,
    args: [routeId],
  });
  const r = rs.rows[0];
  if (!r) return null;
  return {
    startedAt: String(r["started_at"]),
    finishedAt: r["finished_at"] ? String(r["finished_at"]) : null,
    trigger: String(r["trigger"]),
    sources: String(r["sources"]),
    rowsStored: Number(r["rows_stored"]),
    alertsFired: Number(r["alerts_fired"]),
    status: String(r["status"]),
  };
}

/** Mirrors ui/_common.top_alternatives (searchapi/both branch) with
 *  collapse_by_departure=true: cheapest latest price per itinerary,
 *  window + stay filtered, then keep the cheapest row per
 *  (origin, departure_date) — variety across departure days; the full
 *  2D picture lives in the heatmap. */
export async function getTopAlternatives(
  w: RouteWindow,
  limit = 10,
): Promise<Itinerary[]> {
  const fetchLimit = limit * 12;
  const rs = await db().execute({
    sql: `
      SELECT cs.origin, cs.destination, cs.departure_date, cs.return_date,
             cs.stay_days, cs.price, cs.currency, cs.source, cs.snapshot_at,
             (SELECT carriers FROM point_queries pq
              WHERE pq.route_id = cs.route_id AND pq.origin = cs.origin
                AND pq.destination = cs.destination
                AND pq.departure_date = cs.departure_date
                AND pq.return_date = cs.return_date AND pq.rank = 0
              ORDER BY pq.snapshot_at DESC LIMIT 1) AS top_carrier,
             (SELECT stops FROM point_queries pq
              WHERE pq.route_id = cs.route_id AND pq.origin = cs.origin
                AND pq.destination = cs.destination
                AND pq.departure_date = cs.departure_date
                AND pq.return_date = cs.return_date AND pq.rank = 0
              ORDER BY pq.snapshot_at DESC LIMIT 1) AS stops,
             (SELECT total_minutes FROM point_queries pq
              WHERE pq.route_id = cs.route_id AND pq.origin = cs.origin
                AND pq.destination = cs.destination
                AND pq.departure_date = cs.departure_date
                AND pq.return_date = cs.return_date AND pq.rank = 0
              ORDER BY pq.snapshot_at DESC LIMIT 1) AS total_minutes,
             (SELECT is_self_transfer FROM point_queries pq
              WHERE pq.route_id = cs.route_id AND pq.origin = cs.origin
                AND pq.destination = cs.destination
                AND pq.departure_date = cs.departure_date
                AND pq.return_date = cs.return_date AND pq.rank = 0
              ORDER BY pq.snapshot_at DESC LIMIT 1) AS is_self_transfer
      FROM calendar_snapshots cs
      JOIN (
          SELECT source, origin, destination, departure_date, return_date,
                 MAX(snapshot_at) AS latest
          FROM calendar_snapshots
          WHERE route_id = ?
          GROUP BY source, origin, destination, departure_date, return_date
      ) m
        ON m.source = cs.source AND m.origin = cs.origin
       AND m.destination = cs.destination
       AND m.departure_date = cs.departure_date
       AND m.return_date = cs.return_date
       AND m.latest = cs.snapshot_at
      WHERE cs.route_id = ?
        AND cs.stay_days BETWEEN ? AND ?
        AND cs.departure_date >= ?
        AND cs.return_date <= ?
        AND cs.snapshot_at >= ?
      ORDER BY cs.price ASC
      LIMIT ?`,
    args: [
      w.routeId, w.routeId, w.minStay, w.maxStay,
      w.earliestDeparture, w.latestReturn, freshCutoff(), fetchLimit,
    ],
  });
  const seen = new Set<string>();
  const out: Itinerary[] = [];
  for (const r of rs.rows) {
    const key = `${r["origin"]}|${r["departure_date"]}`;
    if (seen.has(key)) continue; // rows are price-ascending: first = cheapest
    seen.add(key);
    out.push({
      origin: String(r["origin"]),
      destination: String(r["destination"]),
      departureDate: String(r["departure_date"]),
      returnDate: String(r["return_date"]),
      stayDays: Number(r["stay_days"]),
      price: Number(r["price"]),
      currency: String(r["currency"]),
      source: String(r["source"]),
      snapshotAt: String(r["snapshot_at"]),
      topCarrier: r["top_carrier"] ? String(r["top_carrier"]) : null,
      stops: r["stops"] === null ? null : Number(r["stops"]),
      totalMinutes:
        r["total_minutes"] === null ? null : Number(r["total_minutes"]),
      isSelfTransfer: Number(r["is_self_transfer"] ?? 0) === 1,
    });
    if (out.length >= limit) break;
  }
  return out;
}

/** Mirrors lib/db.recent_alerts (ORDER BY fired_at DESC LIMIT ?). */
export async function getRecentAlerts(
  routeId: string = DEFAULT_ROUTE,
  limit = 12,
): Promise<Alert[]> {
  const rs = await db().execute({
    sql: `SELECT fired_at, alert_type, source, origin, destination,
                 departure_date, return_date, price, currency,
                 baseline_median, drop_pct
          FROM alerts WHERE route_id = ?
          ORDER BY fired_at DESC, price ASC LIMIT ?`,
    args: [routeId, limit],
  });
  return rs.rows.map((r) => ({
    firedAt: String(r["fired_at"]),
    alertType: String(r["alert_type"]),
    source: String(r["source"]),
    origin: String(r["origin"]),
    destination: String(r["destination"]),
    departureDate: String(r["departure_date"]),
    returnDate: String(r["return_date"]),
    price: Number(r["price"]),
    currency: String(r["currency"]),
    baselineMedian: Number(r["baseline_median"]),
    dropPct: Number(r["drop_pct"]),
  }));
}

/** Mirrors ui/_common.recent_alert_count. */
export async function getAlertCount(
  routeId: string = DEFAULT_ROUTE,
  days = 7,
): Promise<number> {
  const since = new Date(Date.now() - days * 86_400_000)
    .toISOString()
    .replace(/\.\d{3}Z$/, "Z");
  const rs = await db().execute({
    sql: "SELECT COUNT(*) AS n FROM alerts WHERE route_id = ? AND fired_at >= ?",
    args: [routeId, since],
  });
  return Number(rs.rows[0]?.["n"] ?? 0);
}

/** Mirrors ui/_common.latest_grid_for_heatmap: cheapest most-recent price
 *  per (departure_date, stay_days) for one origin, window-clamped. */
export async function getHeatmapGrid(
  w: RouteWindow,
  origin: string,
): Promise<HeatmapCell[]> {
  const rs = await db().execute({
    sql: `
      SELECT cs.departure_date, cs.stay_days, MIN(cs.price) AS price
      FROM calendar_snapshots cs
      JOIN (
          SELECT source, origin, destination, departure_date, return_date,
                 MAX(snapshot_at) AS latest
          FROM calendar_snapshots
          WHERE route_id = ? AND origin = ?
          GROUP BY source, origin, destination, departure_date, return_date
      ) m
        ON m.source = cs.source
       AND m.origin = cs.origin
       AND m.destination = cs.destination
       AND m.departure_date = cs.departure_date
       AND m.return_date = cs.return_date
       AND m.latest = cs.snapshot_at
      WHERE cs.route_id = ? AND cs.origin = ?
        AND cs.stay_days BETWEEN ? AND ?
        AND cs.departure_date >= ?
        AND cs.return_date <= ?
        AND cs.snapshot_at >= ?
      GROUP BY cs.departure_date, cs.stay_days
      ORDER BY cs.departure_date ASC, cs.stay_days ASC`,
    args: [
      w.routeId, origin, w.routeId, origin,
      w.minStay, w.maxStay, w.earliestDeparture, w.latestReturn, freshCutoff(),
    ],
  });
  return rs.rows.map((r) => ({
    departureDate: String(r["departure_date"]),
    stayDays: Number(r["stay_days"]),
    price: Number(r["price"]),
  }));
}

/** Mirrors ui/_common.carrier_mix — rank-0 point queries grouped by the
 *  verbatim carrier string; EXISTS (not JOIN) so repeated snapshots of
 *  one itinerary don't multi-count. */
export async function getCarrierMix(w: RouteWindow): Promise<CarrierCount[]> {
  const rs = await db().execute({
    sql: `
      SELECT pq.carriers AS carriers, COUNT(*) AS n
      FROM point_queries pq
      WHERE pq.route_id = ?
        AND pq.rank = 0
        AND EXISTS (
            SELECT 1 FROM calendar_snapshots cs
            WHERE cs.route_id = ?
              AND cs.origin = pq.origin AND cs.destination = pq.destination
              AND cs.departure_date = pq.departure_date
              AND cs.return_date = pq.return_date
              AND cs.stay_days BETWEEN ? AND ?
              AND cs.departure_date >= ?
              AND cs.return_date <= ?
        )
      GROUP BY pq.carriers
      ORDER BY n DESC
      LIMIT 12`,
    args: [
      w.routeId, w.routeId, w.minStay, w.maxStay,
      w.earliestDeparture, w.latestReturn,
    ],
  });
  return rs.rows.map((r) => ({
    carrier: String(r["carriers"]),
    n: Number(r["n"]),
  }));
}

/** Latest point-query detail (ranks 0..2) for one itinerary — the same
 *  lookup top_alternatives does per-row, expanded to all ranks of the
 *  most recent snapshot. */
export async function getItineraryDetail(
  routeId: string,
  origin: string,
  destination: string,
  departureDate: string,
  returnDate: string,
): Promise<
  {
    rank: number;
    price: number;
    currency: string;
    carriers: string;
    stops: number | null;
    totalMinutes: number | null;
    isSelfTransfer: boolean;
    source: string;
    snapshotAt: string;
  }[]
> {
  const rs = await db().execute({
    sql: `SELECT rank, price, currency, carriers, stops, total_minutes,
                 is_self_transfer, source, snapshot_at
          FROM point_queries
          WHERE route_id = ? AND origin = ? AND destination = ?
            AND departure_date = ? AND return_date = ?
            AND snapshot_at = (
                SELECT MAX(snapshot_at) FROM point_queries
                WHERE route_id = ? AND origin = ? AND destination = ?
                  AND departure_date = ? AND return_date = ?
            )
          ORDER BY rank ASC LIMIT 3`,
    args: [
      routeId, origin, destination, departureDate, returnDate,
      routeId, origin, destination, departureDate, returnDate,
    ],
  });
  return rs.rows.map((r) => ({
    rank: Number(r["rank"]),
    price: Number(r["price"]),
    currency: String(r["currency"]),
    carriers: String(r["carriers"]),
    stops: r["stops"] === null ? null : Number(r["stops"]),
    totalMinutes:
      r["total_minutes"] === null ? null : Number(r["total_minutes"]),
    isSelfTransfer: Number(r["is_self_transfer"] ?? 0) === 1,
    source: String(r["source"]),
    snapshotAt: String(r["snapshot_at"]),
  }));
}

/** Alerts fired for one itinerary (any source). Same table recent_alerts
 *  reads, narrowed to the itinerary key. */
export async function getItineraryAlerts(
  routeId: string,
  origin: string,
  destination: string,
  departureDate: string,
  returnDate: string,
): Promise<Alert[]> {
  const rs = await db().execute({
    sql: `SELECT fired_at, alert_type, source, origin, destination,
                 departure_date, return_date, price, currency,
                 baseline_median, drop_pct
          FROM alerts
          WHERE route_id = ? AND origin = ? AND destination = ?
            AND departure_date = ? AND return_date = ?
          ORDER BY fired_at DESC LIMIT 20`,
    args: [routeId, origin, destination, departureDate, returnDate],
  });
  return rs.rows.map((r) => ({
    firedAt: String(r["fired_at"]),
    alertType: String(r["alert_type"]),
    source: String(r["source"]),
    origin: String(r["origin"]),
    destination: String(r["destination"]),
    departureDate: String(r["departure_date"]),
    returnDate: String(r["return_date"]),
    price: Number(r["price"]),
    currency: String(r["currency"]),
    baselineMedian: Number(r["baseline_median"]),
    dropPct: Number(r["drop_pct"]),
  }));
}

export interface QuotaCard {
  source: string;
  remaining: number | null;
  limitTotal: number | null;
  checkedAt: string;
  resetsAt: string | null;
}

/** Mirrors lib/db.latest_quota per source; Kiwi/RapidAPI reset date is
 *  derived from the X-RateLimit-*-Reset header (seconds-to-reset at
 *  capture time) stored in raw_json. */
export async function getQuotas(): Promise<QuotaCard[]> {
  const sources = ["serpapi", "kiwi", "aviasales", "skyscanner", "searchapi"];
  const out: QuotaCard[] = [];
  for (const source of sources) {
    const rs = await db().execute({
      sql: `SELECT checked_at, remaining, limit_total, raw_json
            FROM quota_snapshots WHERE source = ?
            ORDER BY checked_at DESC LIMIT 1`,
      args: [source],
    });
    const r = rs.rows[0];
    if (!r) continue;
    let resetsAt: string | null = null;
    try {
      const raw = JSON.parse(String(r["raw_json"] ?? "{}"));
      const seconds = findResetSeconds(raw);
      if (seconds !== null) {
        resetsAt = new Date(
          Date.parse(String(r["checked_at"])) + seconds * 1000,
        ).toISOString();
      }
    } catch {
      /* raw_json unparseable -> no reset info */
    }
    out.push({
      source,
      remaining: r["remaining"] === null ? null : Number(r["remaining"]),
      limitTotal: r["limit_total"] === null ? null : Number(r["limit_total"]),
      checkedAt: String(r["checked_at"]),
      resetsAt,
    });
  }
  return out;
}

function findResetSeconds(obj: unknown): number | null {
  if (!obj || typeof obj !== "object") return null;
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    if (/reset/i.test(k)) {
      const n = Number(v);
      if (Number.isFinite(n) && n > 0 && n < 45 * 86_400) return n;
    }
    const nested = findResetSeconds(v);
    if (nested !== null) return nested;
  }
  return null;
}

/** Last N scan_runs rows for the ops history table. */
export async function getScanHistory(
  routeId: string = DEFAULT_ROUTE,
  limit = 10,
): Promise<ScanRun[]> {
  const rs = await db().execute({
    sql: `SELECT started_at, finished_at, trigger, sources, rows_stored,
                 alerts_fired, status
          FROM scan_runs WHERE route_id = ?
          ORDER BY started_at DESC LIMIT ?`,
    args: [routeId, limit],
  });
  return rs.rows.map((r) => ({
    startedAt: String(r["started_at"]),
    finishedAt: r["finished_at"] ? String(r["finished_at"]) : null,
    trigger: String(r["trigger"]),
    sources: String(r["sources"]),
    rowsStored: Number(r["rows_stored"]),
    alertsFired: Number(r["alerts_fired"]),
    status: String(r["status"]),
  }));
}

/** Mirrors ui/_common.itinerary_history_chart's query. */
export async function getItineraryHistory(
  routeId: string,
  origin: string,
  destination: string,
  departureDate: string,
  returnDate: string,
): Promise<HistoryPoint[]> {
  const rs = await db().execute({
    sql: `SELECT snapshot_at, source, price FROM calendar_snapshots
          WHERE route_id = ? AND origin = ? AND destination = ?
            AND departure_date = ? AND return_date = ?
          ORDER BY snapshot_at ASC`,
    args: [routeId, origin, destination, departureDate, returnDate],
  });
  return rs.rows.map((r) => ({
    snapshotAt: String(r["snapshot_at"]),
    source: String(r["source"]),
    price: Number(r["price"]),
  }));
}
