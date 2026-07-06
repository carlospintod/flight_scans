/** Shapes shared by queries and components. */

export interface RouteWindow {
  routeId: string;
  origins: string[];
  destinations: string[];
  earliestDeparture: string; // YYYY-MM-DD
  latestReturn: string;
  minStay: number;
  maxStay: number;
  currency: string;
  watchBelowPrice: number | null;
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

export interface Alert {
  firedAt: string;
  alertType: "drop" | "new_low" | string;
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
