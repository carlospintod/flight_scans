/** Shapes shared by queries and components. */

export interface RouteWindow {
  routeId: string;
  origins: string[];
  destinations: string[];
  earliestDeparture: string; // YYYY-MM-DD
  latestReturn: string;      // for one-way this is the latest DEPARTURE
  /** Optional departure cap (search_window.latest_departure); null = none.
   *  Use lastDeparture() in lib/window.ts rather than reading this raw. */
  latestDeparture: string | null;
  minStay: number;
  maxStay: number;
  /** Stay sampling step (stay_preferences.step_days; 1 = every stay).
   *  Use stayLengths() in lib/window.ts for the sampled set. */
  stayStep: number;
  /** Set when the dates above are the resolution of a rolling window
   *  (search_window.rolling): the dates as of the last scan's load. */
  rolling: RollingSpec | null;
  currency: string;
  watchBelowPrice: number | null;
  tripType: "round_trip" | "one_way";
}

export interface RollingSpec {
  startMonths: number;
  spanMonths: number;
  notBefore: string | null;
  stepDays: number;
}

export interface Itinerary {
  origin: string;
  destination: string;
  departureDate: string;
  returnDate: string;
  stayDays: number;
  price: number;
  currency: string;
  source: string;
  snapshotAt: string;
  topCarrier: string | null;
  stops: number | null;
  totalMinutes: number | null;
  isSelfTransfer: boolean;
  /** The OTA/agency Google lists as the cheapest seller (e.g. SmartFares,
   *  Gotogate) when it undercuts the airline headline; null otherwise. */
  seller: string | null;
}

export interface ScanRun {
  startedAt: string;
  finishedAt: string | null;
  trigger: string;
  sources: string;
  rowsStored: number;
  alertsFired: number;
  status: string;
}

export interface SourceHealthCard {
  source: string;
  verdict: string; // live|degraded|quota_low|dark|payment_walled|auth_failed|idle|unknown|disabled
  detail: string;
  attempts: number;
  ok: number;
  stored: number;
  lastOkAt: string | null;
  available: number | null;
}

export interface Alert {
  firedAt: string;
  alertType: "drop" | "new_low" | "window_best" | string;
  source: string;
  origin: string;
  destination: string;
  departureDate: string;
  returnDate: string;
  price: number;
  currency: string;
  baselineMedian: number;
  dropPct: number;
}

export interface HeatmapCell {
  departureDate: string;
  stayDays: number;
  price: number;
}

export interface CarrierCount {
  carrier: string;
  n: number;
}

export interface HistoryPoint {
  snapshotAt: string;
  source: string;
  price: number;
}
