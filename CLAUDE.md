# Flexible Flight Tracker

A configurable price tracker for flexible-date round-trip flights. Scans a date rectangle across a target window, tracks how prices evolve over time, and alerts on drops below a per-itinerary baseline.

The first run targets **Spain (MAD, BCN) ↔ Nairobi (NBO)** for the user's own travel — but the design is route-agnostic. Same code runs for any future flexible-dates corridor (e.g. Spain → Japan, 14–21 day stays, summer only) by swapping a single config file.

---

## Goal

Detect price drops on flexible-date round-trips early enough to act on them. The user is highly flexible on dates within a target window; what matters is finding the cheapest combination of `(departure_date, return_date)` for a given route and acceptable stay range. The tracker is **not** a booking tool — it captures signal, the user books manually.

## Non-goals

- Booking, payment, passenger management
- Alerting at the minute level (daily granularity is sufficient)
- Hard-coding any single route — everything route-specific lives in config

---

## First-run configuration (Spain ↔ Nairobi)

```yaml
route:
  name: spain-nairobi
  origins: [MAD, BCN]
  destinations: [NBO]

search_window:
  earliest_departure: 2026-06-01
  latest_return: 2027-05-31

stay_preferences:
  min_days: 30
  max_days: 60

currency: EUR

sweep:
  outbound_window_days: 14
  return_window_days: 14
  overlap_days: 3
  cadence_days: 14   # biweekly

alerts:
  drop_threshold_pct: 15
  baseline_window_days: 30
  min_observations: 4
```

The user typically targets sub-€500 round-trips, achievable in low season on certain carriers (see Validated Corridor Characteristics).

---

## Data source: SearchAPI.io (Google Flights)

After surveying alternatives (Amadeus Self-Service shut down to new registrations as of March 2026; Duffel limits initial carrier access; SerpAPI at $75/month exceeds budget), **SearchAPI.io's free tier** is the chosen source.

- **Endpoint base**: `https://www.searchapi.io/api/v1/search`
- **Auth**: API key, passed as `api_key` query parameter (or `Authorization: Bearer <key>` header)
- **Free tier**: 100 successful searches per month
- **Two engines used**:
  - `engine=google_flights` — full carrier/price detail for one specific (outbound, return) pair
  - `engine=google_flights_calendar` — price grid across a (departure_range × return_range) rectangle

The API key is stored in a `.env` file (gitignored) as `SEARCHAPI_KEY=...`.

### Validated corridor characteristics for Spain ↔ Nairobi

From the initial probe and manual Google Flights inspection:

- **Carriers visible in Google Flights for MAD-NBO**: Air France, British Airways, Brussels Airlines, Emirates, Etihad, EgyptAir, Iberia, Kenya Airways, KLM, Lufthansa, Qatar Airways, Turkish Airlines.
- **Saudia (SV) does NOT appear in Google Flights for this corridor.** This is a known structural gap. Any Saudia-specific deals (e.g. promotional pricing on JED-NBO) will be invisible to this tracker. Acceptable trade-off given it's the only excluded major carrier.
- **No sub-€500 prices were visible in mid-2026 inspection.** Historical user experience suggests sub-€500 deals do appear seasonally on Etihad, EgyptAir, Kenya Airways, but did not surface on the dates checked. The tracker should run across the full year so seasonal lows are captured if they appear.
- **`price_insights` block does NOT populate** for this corridor. We must build the price history ourselves from repeated point queries — no free historical-data shortcut available from the API.

### Hard constraint discovered

The `google_flights_calendar` endpoint **caps at 200 (departure × return) combinations per call**. Wider ranges return HTTP 400 with a clear error message. This dictates how we chunk calendar sweeps — see architecture below. A 14×14 window (196 combos) is the sweet spot.

---

## Architecture

### Core principle: don't constrain stay length at the API level

Naive approach: query only itineraries where `return - departure` falls within the desired stay range (30–60 days). This is rigid and misses cross-window deals — e.g. depart Sep 3, return Nov 6, a 64-day stay that would be filtered out by a strict 60-day cap but is genuinely interesting.

Correct approach: query loose rectangles, capture every priced combination the API returns, **filter for desired stay length at read time**. The calendar endpoint returns whatever combinations exist within the rectangle you give it, regardless of stay length. We store everything and let the analysis layer decide what's interesting.

### Tiered query strategy

Two layers, each with a distinct purpose. Calendar sweeps tell us **where** the cheap pockets are at a moment in time. Point queries tell us **whether** a flagged itinerary is unusually cheap by historical standards.

#### Tier 1 — Sliding-window calendar sweeps (discovery)

Goal: find cheap `(departure, return)` combinations across the entire search window, across all origins.

- Engine: `google_flights_calendar`
- Each call uses a 14-day outbound × 14-day return rectangle = 196 combinations (under the 200-combo cap).
- Windows **slide across the search window with overlap** (default 3 days) to avoid edge effects.
- For a 12-month search window, ~12–14 calls per origin per sweep covers everything. Two origins × biweekly cadence ≈ **50 calls/month**.
- Stay-length filtering is NOT applied at query time. We let the rectangle return everything.

Each calendar entry is small: `{departure, return, price, has_no_flights, is_lowest_price}`. Store every priced entry as a snapshot row.

#### Tier 2 — Point queries (trend confirmation)

Goal: build a price-over-time series for itineraries Tier 1 has flagged as interesting.

- Engine: `google_flights`
- Triggered when Tier 1 surfaces an itinerary that:
  - Falls within the configured `stay_preferences.min_days` to `max_days` range
  - Is flagged with `is_lowest_price`, OR is priced below the trailing baseline by `drop_threshold_pct` (when ≥ `min_observations` prior snapshots exist)
- Capture: `best_flights[0..2]` (carriers, stops, total duration, price), full timestamp.
- Run frequency: ~3–5 flagged itineraries × weekly = **~15–20 calls/month**.

#### Monthly footprint

| Layer | Calls/month | Purpose |
|---|---|---|
| Tier 1 (calendar sweeps) | ~50 | Discover cheap date pockets |
| Tier 2 (point queries on signals) | ~15–20 | Build per-itinerary trend over time |
| Reserve / ad-hoc | ~30 | Manual investigation, densification on hot signals |
| **Total** | **~70–80 routine, ≤100 cap** | Inside SearchAPI.io free tier |

If the budget is exceeded once the project runs, the cheapest tightening is to slow Tier 1 cadence from biweekly to monthly (halves Tier 1 cost) before reducing window coverage.

---

## Storage

SQLite (single file, no server). Two tables, plus a routes table to support multiple concurrent route configurations later.

```sql
CREATE TABLE routes (
    route_id     TEXT PRIMARY KEY,    -- matches config 'route.name', e.g. 'spain-nairobi'
    config_json  TEXT NOT NULL,       -- full config snapshot at last run
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE calendar_snapshots (
    snapshot_at      TEXT NOT NULL,    -- ISO timestamp of the sweep
    route_id         TEXT NOT NULL,
    origin           TEXT NOT NULL,
    destination      TEXT NOT NULL,
    departure_date   TEXT NOT NULL,    -- YYYY-MM-DD
    return_date      TEXT NOT NULL,    -- YYYY-MM-DD
    stay_days        INTEGER NOT NULL, -- derived: return - departure
    price            INTEGER NOT NULL,
    currency         TEXT NOT NULL,
    is_lowest_price  INTEGER NOT NULL  -- 0 or 1
);

CREATE INDEX idx_cal_itin ON calendar_snapshots (route_id, origin, destination, departure_date, return_date);
CREATE INDEX idx_cal_time ON calendar_snapshots (snapshot_at);

CREATE TABLE point_queries (
    snapshot_at      TEXT NOT NULL,
    route_id         TEXT NOT NULL,
    origin           TEXT NOT NULL,
    destination      TEXT NOT NULL,
    departure_date   TEXT NOT NULL,
    return_date      TEXT NOT NULL,
    rank             INTEGER NOT NULL, -- 0 = best, 1 = second best, etc.
    price            INTEGER NOT NULL,
    currency         TEXT NOT NULL,
    carriers         TEXT NOT NULL,    -- e.g. "Qatar Airways" or "KLM + Kenya Airways"
    total_minutes    INTEGER,
    stops            INTEGER
);

CREATE INDEX idx_pq_itin ON point_queries (route_id, origin, destination, departure_date, return_date);
CREATE INDEX idx_pq_time ON point_queries (snapshot_at);

CREATE TABLE alerts (
    fired_at         TEXT NOT NULL,
    route_id         TEXT NOT NULL,
    origin           TEXT NOT NULL,
    destination      TEXT NOT NULL,
    departure_date   TEXT NOT NULL,
    return_date      TEXT NOT NULL,
    price            INTEGER NOT NULL,
    currency         TEXT NOT NULL,
    baseline_median  INTEGER NOT NULL,
    drop_pct         REAL NOT NULL
);
```

Currency is stored explicitly per row (not assumed from config) so future re-runs in different currencies don't corrupt history.

---

## Alerting

A drop is interesting if **all three** are true:

1. Stay length falls within `[stay_preferences.min_days, stay_preferences.max_days]`
2. Price is below the trailing-`baseline_window_days` median for that same `(route_id, origin, destination, departure_date, return_date)` itinerary by at least `drop_threshold_pct`
3. We have at least `min_observations` prior snapshots for that itinerary (avoids alerting on noise from a freshly-seen entry)

Initial alert channels:
- Append to `data/alerts.log` (one line per alert, grep-friendly)
- Print to stdout when running interactively

A Telegram bot or email can come later if useful — keep the alert sink modular.

---

## Stack

- **Python 3.14** (already on user's machine)
- `requests` for HTTP
- `sqlite3` (stdlib) for storage
- `python-dotenv` for the API key
- `pyyaml` for config parsing
- Single CLI script with subcommands:
  - `python tracker.py sweep --route spain-nairobi` — Tier 1 calendar sweep
  - `python tracker.py followup --route spain-nairobi` — Tier 2 point queries on flagged itineraries
  - `python tracker.py alerts --route spain-nairobi` — evaluate alert conditions, write to log
  - `python tracker.py report --route spain-nairobi` — print recent alerts and current cheapest itineraries

Scheduling is external to the script (Windows Task Scheduler in v1). Biweekly for sweeps, weekly for followups, daily for alerts evaluation.

---

## Project structure

```
flight-tracker/
├── CLAUDE.md
├── README.md
├── .env.example                 # SEARCHAPI_KEY=
├── .gitignore                   # .env, data/*.db, response_*.json
├── tracker.py                   # CLI entry point
├── routes/
│   └── spain-nairobi.yaml       # first-run config; one file per route
├── lib/
│   ├── __init__.py
│   ├── config.py                # config schema, loader, validator
│   ├── api.py                   # SearchAPI.io client, both engines
│   ├── db.py                    # SQLite schema, inserts, queries
│   ├── sweep.py                 # Tier 1 logic: window planning, calendar calls
│   ├── followup.py              # Tier 2 logic: candidate selection, point calls
│   ├── alerts.py                # threshold detection, alert sink
│   └── report.py                # CLI output formatting
├── data/
│   └── tracker.db               # gitignored
└── tests/
    ├── fixtures/
    │   ├── calendar_response.json   # from initial probe (key scrubbed)
    │   └── flights_response.json    # from initial probe (key scrubbed)
    └── test_*.py                # unit tests for window planning, baseline calc, alert logic
```

---

## Working principles for this project

These match the user's preferred style across other projects:

- **Verify signals against documentation or real responses before coding.** No fabricated detection patterns or invented response shapes.
- **Use the captured probe responses as test fixtures** (with API keys scrubbed). Unit tests for window planning, baseline calculation, and alert logic should run against fixtures, not the live API.
- **Plan methodology before writing code.** When in doubt, ask before assuming scope.
- **No emojis, no unnecessary verbosity.** Production logging in particular should be terse and grep-friendly.
- **No premature optimisation.** v1 is correctness; performance work waits until volume actually demands it.
- **Route-agnostic from day one.** Even though only spain-nairobi runs at first, no hardcoded `MAD`, `NBO`, `EUR`, or stay numbers anywhere except the YAML config.

---

## Build order

Each step is a discrete, committable unit. Each step should run end-to-end against the real API (using ~1–2 calls of the monthly budget) before moving to the next.

1. **Skeleton + dependencies.** `pyproject.toml` or `requirements.txt`, `.env.example`, `.gitignore`, empty `tracker.py` with subcommand stubs.
2. **Config layer.** `lib/config.py` reads `routes/spain-nairobi.yaml`, validates structure, returns a typed config object. Test against the YAML.
3. **API client.** `lib/api.py` wraps both engines, parses JSON, surfaces errors clearly. Verify against fixtures, then one live call per engine.
4. **DB layer.** `lib/db.py` creates schema, inserts rows. No business logic.
5. **Sweep command.** `lib/sweep.py` plans the sliding windows from config, fires calendar calls (start with one origin and one window for cost control), persists rows. Verify, then expand to all origins/windows.
6. **Followup command.** `lib/followup.py` reads recent calendar rows, applies stay-range and trigger filters, fires point queries, persists rows.
7. **Alerts command.** `lib/alerts.py` evaluates the three-condition rule per itinerary, writes to `alerts` table and log file.
8. **Report command.** `lib/report.py` prints the cheapest itineraries within stay range grouped by month, plus active alerts.
9. **(Optional)** Telegram or email sink for alerts. Streamlit dashboard.

---

## Open questions to revisit later

- Whether re-testing on different dates ever surfaces Saudia for MAD-NBO. If consistently absent, document as a permanent corridor gap and stop checking.
- Whether sliding-window overlap should be tuned higher than 3 days (more redundancy, more calls) or lower (fewer calls, edge risk). Decide once we see real-world drift.
- Whether to add a "dry run" mode that simulates a sweep using cached fixtures for development without burning API quota.
- Whether the tracker should also capture **one-way** prices as a secondary signal for trips where stay length is unusually flexible.
