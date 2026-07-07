import { NextResponse, type NextRequest } from "next/server";
import { canonicalJson } from "@/lib/canonical";
import { RUNS_PER_MONTH, capacityView } from "@/lib/capacity";
import { db } from "@/lib/db";
import { predictUpperBounds } from "@/lib/predict";
import { requireUser } from "@/lib/users";

const MAX_ACTIVE_PER_USER = 2;
const IATA = /^[A-Z]{3}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

function slug(): string {
  const alphabet = "abcdefghjkmnpqrstuvwxyz23456789"; // no lookalikes
  const buf = new Uint8Array(8);
  crypto.getRandomValues(buf);
  return [...buf].map((b) => alphabet[b % alphabet.length]).join("");
}

/** Create a search: one routes row (canonical config_json, name==slug —
 *  the zero-migration partition key) + one searches ownership row.
 *  Capacity and per-user caps are enforced HERE for UX; the batch
 *  planner enforces them again at reserve time (defense in depth — the
 *  planner is what spends money). */
export async function POST(req: NextRequest) {
  const user = await requireUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  let b: Record<string, unknown> = {};
  try {
    b = await req.json();
  } catch { /* validated below */ }

  const origin = String(b.origin ?? "").toUpperCase().trim();
  const destination = String(b.destination ?? "").toUpperCase().trim();
  const earliest = String(b.earliestDeparture ?? "");
  const oneWay = b.tripType === "one_way";
  // One-way has no return leg: the "latest return" field is repurposed as
  // the latest DEPARTURE the user will accept, and stay is irrelevant.
  const latestReturn = String(b.latestReturn ?? "");
  const minStay = oneWay ? 0 : Number(b.minStay);
  const maxStay = oneWay ? 0 : Number(b.maxStay);

  const problems: string[] = [];
  if (!IATA.test(origin)) problems.push("origin must be a 3-letter airport code");
  if (!IATA.test(destination)) problems.push("destination must be a 3-letter airport code");
  if (origin === destination) problems.push("origin and destination must differ");
  if (!DATE.test(earliest) || !DATE.test(latestReturn)) problems.push("dates must be YYYY-MM-DD");
  if (!oneWay) {
    if (!Number.isInteger(minStay) || minStay < 1) problems.push("min stay must be ≥ 1");
    if (!Number.isInteger(maxStay) || maxStay < minStay) problems.push("max stay must be ≥ min stay");
  }
  if (DATE.test(earliest) && DATE.test(latestReturn)) {
    if (Date.parse(latestReturn) <= Date.parse(earliest)) {
      problems.push(oneWay
        ? "latest departure must be after earliest departure"
        : "latest return must be after earliest departure");
    }
    if (Date.parse(earliest) < Date.now() - 86_400_000) {
      problems.push("earliest departure is in the past");
    }
    if (!oneWay) {
      const span = (Date.parse(latestReturn) - Date.parse(earliest)) / 86_400_000;
      if (maxStay > span) problems.push("max stay doesn't fit inside the window");
    }
  }
  if (problems.length) {
    return NextResponse.json({ error: problems.join("; ") }, { status: 400 });
  }

  const client = db();

  const mine = await client.execute({
    sql: `SELECT COUNT(*) AS n FROM searches
          WHERE user_id = ? AND status = 'active'`,
    args: [user.userId],
  });
  if (Number(mine.rows[0]["n"]) >= MAX_ACTIVE_PER_USER
      && user.role !== "owner") {
    return NextResponse.json(
      { error: `you already have ${MAX_ACTIVE_PER_USER} active searches — pause one first` },
      { status: 409 },
    );
  }

  // Shared-pool verdict (B5: against ALL active searches' committed load).
  const predicted = predictUpperBounds({
    nOrigins: 1, nDestinations: 1,
    earliestDeparture: earliest, latestReturn, minStayDays: minStay,
    tripType: oneWay ? "one_way" : "round_trip",
  });
  const cap = await capacityView();
  const newMonthly = predicted.kiwi * RUNS_PER_MONTH;
  if (cap.kiwi.available !== null
      && cap.kiwi.committedMonthly + newMonthly > cap.kiwi.available
      && user.role !== "owner") {
    return NextResponse.json(
      { error: "not enough shared discovery capacity for this search right " +
               "now — try a narrower date window, or ask the owner about " +
               "expanding capacity" },
      { status: 409 },
    );
  }

  const id = slug();
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  const config: Record<string, unknown> = {
    route: { name: id, origins: [origin], destinations: [destination] },
    search_window: {
      earliest_departure: earliest, latest_return: latestReturn,
    },
    stay_preferences: { min_days: minStay, max_days: maxStay },
    currency: "EUR",
    sweep: { cadence_days: 3 },
    // Price-mode thresholds intentionally wide open for a fresh search:
    // "anything ever observed qualifies" — candidate selection is
    // cheapest-first + month-diversified + capped, so verification
    // tracks the cheapest combos found. The user has no baseline yet
    // to threshold against; tightening comes later via alerts data.
    followup: { watch_below_price: 999999, drop_above_price: 999999 },
    alerts: { drop_threshold_pct: 15, baseline_window_days: 30,
              min_observations: 4 },
  };
  // Emit trip_type ONLY for one_way — round-trip configs stay identical
  // to pre-one-way shape (matches lib/config route_to_yaml_dict, so
  // route_store never rewrites them).
  if (oneWay) config.trip_type = "one_way";

  await client.execute({
    sql: `INSERT INTO routes (route_id, config_json, created_at, updated_at)
          VALUES (?, ?, ?, ?)`,
    args: [id, canonicalJson(config), now, now],
  });
  await client.execute({
    sql: `INSERT INTO searches (search_id, user_id, status, is_public,
                                notify, priority, predicted_json,
                                created_at, updated_at)
          VALUES (?, ?, 'active', 0, 'alerts_only', ?, ?, ?, ?)`,
    args: [id, user.userId, user.role === "owner" ? "owner" : "user",
           JSON.stringify(predicted), now, now],
  });
  return NextResponse.json({ ok: true, searchId: id, predicted });
}
