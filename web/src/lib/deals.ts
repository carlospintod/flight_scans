// Deal-queue reads for /ops and the public archive (Vuelazo M1+).
//
// The deals tables are owned by the Python side (lib/deals_db.py) — this
// module only reads/aggregates. Statuses: candidate | verified | queued |
// approved | rejected | expired | published.

import { db } from "@/lib/db";

export type DealRow = {
  id: number;
  origin: string;
  dest: string;
  sampleDates: string | null;
  price: number;
  currency: string;
  baselineMedian: number | null;
  pctBelow: number | null;
  absSaving: number | null;
  score: number | null;
  dealClass: string;
  status: string;
  draftEs: string | null;
  draftVersion: string | null;
  confidence: { level?: string; score?: number; families?: string[] } | null;
  verificationRefs: { live_price?: number; note?: string; checked_at?: string } | null;
  freePick: boolean;
  createdAt: string;
  approvedAt: string | null;
  publishedAt: string | null;
};

function parseJson<T>(v: unknown): T | null {
  if (typeof v !== "string" || !v) return null;
  try {
    return JSON.parse(v) as T;
  } catch {
    return null;
  }
}

function rowToDeal(r: Record<string, unknown>): DealRow {
  return {
    id: Number(r["id"]),
    origin: String(r["origin"]),
    dest: String(r["dest"]),
    sampleDates: r["sample_dates"] ? String(r["sample_dates"]) : null,
    price: Number(r["price"]),
    currency: String(r["currency"] ?? "EUR"),
    baselineMedian: r["baseline_median"] == null ? null : Number(r["baseline_median"]),
    pctBelow: r["pct_below"] == null ? null : Number(r["pct_below"]),
    absSaving: r["abs_saving"] == null ? null : Number(r["abs_saving"]),
    score: r["score"] == null ? null : Number(r["score"]),
    dealClass: String(r["class"] ?? "standard"),
    status: String(r["status"]),
    draftEs: r["draft_es"] ? String(r["draft_es"]) : null,
    draftVersion: r["draft_version"] ? String(r["draft_version"]) : null,
    confidence: parseJson(r["confidence"]),
    verificationRefs: parseJson(r["verification_refs"]),
    freePick: Number(r["free_pick"] ?? 0) === 1,
    createdAt: String(r["created_at"]),
    approvedAt: r["approved_at"] ? String(r["approved_at"]) : null,
    publishedAt: r["published_at"] ? String(r["published_at"]) : null,
  };
}

/** Cards awaiting the approve tap, oldest first (D3: <=15/day). */
export async function getDealQueue(limit = 20): Promise<DealRow[]> {
  try {
    const rs = await db().execute({
      sql: `SELECT * FROM deals WHERE status IN ('queued', 'approved')
            ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END, created_at
            LIMIT ?`,
      args: [limit],
    });
    return rs.rows.map((r) => rowToDeal(r as Record<string, unknown>));
  } catch {
    return []; // table may not exist on a fresh dev DB
  }
}

export type DealCounts = {
  queued: number;
  approvedUnpublished: number;
  publishedLast7: number;
  rejectedLast7: number;
};

export async function getDealCounts(): Promise<DealCounts> {
  const empty = { queued: 0, approvedUnpublished: 0, publishedLast7: 0, rejectedLast7: 0 };
  try {
    const since = new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 10);
    const rs = await db().execute({
      sql: `SELECT
              SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS q,
              SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS a,
              SUM(CASE WHEN status = 'published' AND published_at >= ? THEN 1 ELSE 0 END) AS p
            FROM deals`,
      args: [since],
    });
    // Rejections are counted from when they HAPPENED (rejections.ts),
    // not from when the deal was created — the D3 tuning signal.
    const rj = await db().execute({
      sql: "SELECT COUNT(*) AS n FROM rejections WHERE ts >= ?",
      args: [since],
    });
    const r = rs.rows[0] as Record<string, unknown>;
    return {
      queued: Number(r["q"] ?? 0),
      approvedUnpublished: Number(r["a"] ?? 0),
      publishedLast7: Number(r["p"] ?? 0),
      rejectedLast7: Number(rj.rows[0]?.["n"] ?? 0),
    };
  } catch {
    return empty;
  }
}

export type SparkPoint = { day: string; price: number };

/** Cheapest cached price per day for one route — the card sparkline
 *  ("el precio normal, demostrado"). */
export async function getRouteSparkline(
  origin: string,
  dest: string,
  days = 60,
): Promise<SparkPoint[]> {
  try {
    const since = new Date(Date.now() - days * 864e5).toISOString().slice(0, 10);
    const rs = await db().execute({
      sql: `SELECT substr(observed_at, 1, 10) AS day, MIN(price) AS price
            FROM fare_observations
            WHERE origin = ? AND dest = ? AND observed_at >= ?
            GROUP BY day ORDER BY day`,
      args: [origin, dest, since],
    });
    return rs.rows.map((r) => ({
      day: String((r as Record<string, unknown>)["day"]),
      price: Number((r as Record<string, unknown>)["price"]),
    }));
  } catch {
    return [];
  }
}

/** Published deals for the public archive (M3/M4a: 24h delayed). */
export async function getPublishedDeals(opts?: {
  origin?: string;
  delayedHours?: number;
  limit?: number;
}): Promise<DealRow[]> {
  const { origin, delayedHours = 0, limit = 50 } = opts ?? {};
  try {
    const cutoff = new Date(Date.now() - delayedHours * 3600e3)
      .toISOString()
      .replace(/\.\d{3}Z$/, "Z");
    const args: (string | number)[] = [cutoff];
    let sql = `SELECT * FROM deals WHERE status = 'published' AND published_at <= ?`;
    if (origin) {
      sql += " AND origin = ?";
      args.push(origin.toUpperCase());
    }
    sql += " ORDER BY published_at DESC LIMIT ?";
    args.push(limit);
    const rs = await db().execute({ sql, args });
    return rs.rows.map((r) => rowToDeal(r as Record<string, unknown>));
  } catch {
    return [];
  }
}
