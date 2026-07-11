"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { RUNS_PER_MONTH } from "@/lib/capacity-constants";
import { predictUpperBounds } from "@/lib/predict";

const AIRPORTS: [string, string][] = [
  ["MAD", "Madrid"], ["BCN", "Barcelona"], ["AGP", "Málaga"],
  ["VLC", "Valencia"], ["LIS", "Lisbon"], ["OPO", "Porto"],
  ["LHR", "London Heathrow"], ["LGW", "London Gatwick"], ["STN", "London Stansted"],
  ["CDG", "Paris CDG"], ["ORY", "Paris Orly"], ["AMS", "Amsterdam"],
  ["FRA", "Frankfurt"], ["MUC", "Munich"], ["BER", "Berlin"],
  ["ZRH", "Zurich"], ["GVA", "Geneva"], ["VIE", "Vienna"],
  ["FCO", "Rome"], ["MXP", "Milan"], ["ATH", "Athens"],
  ["IST", "Istanbul"], ["DXB", "Dubai"], ["AUH", "Abu Dhabi"],
  ["DOH", "Doha"], ["CAI", "Cairo"], ["NBO", "Nairobi"],
  ["ADD", "Addis Ababa"], ["JNB", "Johannesburg"], ["CPT", "Cape Town"],
  ["ZNZ", "Zanzibar"], ["DAR", "Dar es Salaam"], ["JFK", "New York JFK"],
  ["EWR", "Newark"], ["BOS", "Boston"], ["MIA", "Miami"],
  ["LAX", "Los Angeles"], ["SFO", "San Francisco"], ["YYZ", "Toronto"],
  ["MEX", "Mexico City"], ["BOG", "Bogotá"], ["LIM", "Lima"],
  ["EZE", "Buenos Aires"], ["GRU", "São Paulo"], ["SCL", "Santiago"],
  ["NRT", "Tokyo Narita"], ["HND", "Tokyo Haneda"], ["ICN", "Seoul"],
  ["BKK", "Bangkok"], ["SIN", "Singapore"], ["KUL", "Kuala Lumpur"],
  ["DPS", "Bali"], ["DEL", "Delhi"], ["BOM", "Mumbai"],
  ["CMB", "Colombo"], ["MLE", "Malé"], ["SYD", "Sydney"],
  ["MEL", "Melbourne"], ["AKL", "Auckland"],
];

interface Capacity {
  kiwi: { available: number | null; committedMonthly: number };
}

export default function NewSearchPage() {
  const router = useRouter();
  const [tripType, setTripType] = useState<"round_trip" | "one_way">("round_trip");
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [earliest, setEarliest] = useState("");
  const [latestReturn, setLatestReturn] = useState("");
  const [minStay, setMinStay] = useState(7);
  const [maxStay, setMaxStay] = useState(14);
  const [capacity, setCapacity] = useState<Capacity | null>(null);
  const [state, setState] = useState<"idle" | "busy" | { error: string }>(
    "idle");
  const oneWay = tripType === "one_way";

  useEffect(() => {
    fetch("/api/capacity").then(async (r) => {
      if (r.ok) setCapacity(await r.json());
    });
  }, []);

  const complete = /^[A-Za-z]{3}$/.test(origin)
    && /^[A-Za-z]{3}$/.test(destination)
    && earliest !== "" && latestReturn !== ""
    && Date.parse(latestReturn) > Date.parse(earliest)
    && (oneWay || (maxStay >= minStay && minStay >= 1));

  const preview = useMemo(() => {
    if (!complete) return null;
    return predictUpperBounds({
      nOrigins: 1, nDestinations: 1,
      earliestDeparture: earliest, latestReturn,
      minStayDays: oneWay ? 0 : minStay,
      tripType: oneWay ? "one_way" : "round_trip",
    });
  }, [complete, earliest, latestReturn, minStay, oneWay]);

  const fits = useMemo(() => {
    if (!preview || !capacity) return null;
    if (capacity.kiwi.available === null) return null;
    return capacity.kiwi.committedMonthly + preview.kiwi * RUNS_PER_MONTH
      <= capacity.kiwi.available;
  }, [preview, capacity]);

  async function create() {
    setState("busy");
    const r = await fetch("/api/searches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tripType, origin: origin.toUpperCase(),
        destination: destination.toUpperCase(),
        earliestDeparture: earliest, latestReturn, minStay, maxStay,
      }),
    });
    const body = await r.json().catch(() => ({}));
    if (r.ok) {
      router.push(`/s/${body.searchId}`);
      router.refresh();
    } else {
      setState({ error: body.error ?? `HTTP ${r.status}` });
    }
  }

  const label =
    "block font-mono text-[10px] uppercase tracking-wider text-hint mb-1";
  const input =
    "w-full rounded-card border border-border bg-bg px-2.5 py-2.5 font-mono " +
    "text-[14px] text-text-bright outline-none focus:border-signature-dim";

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <div>
        <h1 className="font-mono text-lg text-text-bright">NEW SEARCH</h1>
        <p className="mt-1 text-sm text-text-mid">
          {oneWay
            ? "One-way: your departure floats across the whole window. The tracker hunts the cheapest day to fly out, every scan."
            : "Both your departure and your return float freely — you set the window and how long you want to stay. The tracker hunts the cheapest combination, every scan."}
        </p>
      </div>

      <div className="inline-flex overflow-hidden rounded-card border border-border">
        {(["round_trip", "one_way"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTripType(t)}
            className={`px-4 py-2 font-mono text-[12px] tracking-wider ${
              tripType === t
                ? "bg-bg3 text-signature"
                : "bg-bg2 text-text-mid hover:text-text"
            }`}
          >
            {t === "round_trip" ? "ROUND TRIP" : "ONE WAY"}
          </button>
        ))}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className={label}>from</label>
          <input list="airports" value={origin} maxLength={3}
                 onChange={(e) => setOrigin(e.target.value.toUpperCase())}
                 placeholder="MAD" className={input} autoFocus />
        </div>
        <div>
          <label className={label}>to</label>
          <input list="airports" value={destination} maxLength={3}
                 onChange={(e) => setDestination(e.target.value.toUpperCase())}
                 placeholder="NBO" className={input} />
        </div>
        <datalist id="airports">
          {AIRPORTS.map(([code, city]) => (
            <option key={code} value={code}>{city}</option>
          ))}
        </datalist>
        <div>
          <label className={label}>earliest departure</label>
          <input type="date" value={earliest}
                 onChange={(e) => setEarliest(e.target.value)}
                 className={input} />
        </div>
        <div>
          <label className={label}>
            {oneWay ? "latest departure" : "latest return"}
          </label>
          <input type="date" value={latestReturn}
                 onChange={(e) => setLatestReturn(e.target.value)}
                 className={input} />
        </div>
        {!oneWay && (
          <>
            <div>
              <label className={label}>min stay (days)</label>
              <input type="number" min={1} value={minStay}
                     onChange={(e) => setMinStay(Number(e.target.value))}
                     className={input} />
            </div>
            <div>
              <label className={label}>max stay (days)</label>
              <input type="number" min={minStay} value={maxStay}
                     onChange={(e) => setMaxStay(Number(e.target.value))}
                     className={input} />
            </div>
          </>
        )}
      </div>

      <div className="rounded-card border border-border bg-bg2 p-4">
        <div className="font-mono text-[11px] uppercase tracking-wider text-hint">
          What this search costs — guaranteed upper bound
        </div>
        {preview ? (
          <>
            <p className="mt-2 font-mono text-[13px] text-text">
              ~{preview.kiwi + preview.aviasales} discovery lookups per scan
              · up to {preview.googleflights} verifications per scan ·
              3 scans/week
            </p>
            <details className="mt-2">
              <summary className="cursor-pointer font-mono text-[11px] text-text-mid">
                per-source detail
              </summary>
              <ul className="mt-1 space-y-0.5 font-mono text-[12px] text-text-mid">
                <li>kiwi discovery ≤{preview.kiwi}/scan</li>
                <li>google flights verification ≤{preview.googleflights}/scan</li>
                <li>serpapi contingency ≤{preview.serpapi_contingency}/scan
                  (only if the primary rail fails)</li>
                <li>aviasales cached sweep {preview.aviasales}/scan</li>
              </ul>
            </details>
            <p className={`mt-3 font-mono text-[12px] ${
              fits === false ? "text-red"
              : fits === true ? "text-good" : "text-hint"}`}>
              {fits === true && "✓ fits the shared capacity"}
              {fits === false &&
                "✗ over shared capacity — narrow the window or ask the owner"}
              {fits === null && "capacity check pending…"}
            </p>
          </>
        ) : (
          <p className="mt-2 font-mono text-[12px] text-hint">
            fill the form to see the exact budget
          </p>
        )}
      </div>

      <button
        onClick={create}
        disabled={!complete || state === "busy" || fits === false}
        className="w-full rounded-card border border-signature-dim bg-bg2 px-4 py-2.5 font-mono text-sm font-semibold tracking-wider text-signature hover:shadow-glow-sig disabled:opacity-40"
      >
        {state === "busy" ? "CREATING…" : "▶ START TRACKING"}
      </button>
      {typeof state === "object" && (
        <p className="font-mono text-[12px] text-red">{state.error}</p>
      )}
      <p className="font-mono text-[11px] text-hint">
        First results arrive with the next scheduled scan (Mon/Wed/Sat
        morning). ≤N numbers are hard ceilings — a scan can spend less,
        never more.
      </p>
    </div>
  );
}
