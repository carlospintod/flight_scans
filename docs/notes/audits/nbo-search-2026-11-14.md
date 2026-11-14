# NBO search audit — 2026-11-14

Scope: the Spain–Nairobi tracker only (`spain-nairobi`). Vuelazo is out of scope.
Read-only: no code changed, no provider called. Numbers come from the live Turso DB,
the GitHub Actions logs and the code at `383eda2`.

## What the search is for

Find the cheapest MAD/BCN → NBO round trip leaving 27 Dec 2026 – 1 Mar 2027, staying
60–90 days, back by 30 May 2027, and push to the phone when a real low appears. Free
tiers only. The landing page promises "the single cheapest combination across thousands
of date pairs, three times a week".

The window is 65 departure days × 31 stay lengths = 2,015 date pairs per origin,
4,030 in total. The first departure is six weeks away.

## Verdict

The engine does not do that today. It has priced **21 of 4,030 pairs, ever**, in the
current window (20 in the last 21 days). Each scan adds 5 new pairs and re-checks the
3–5 already known to be under €600. The layer built to price every pair cannot run.

## What one scan does now (11 Nov cron run)

| Rail | Calls | What it did |
|---|---|---|
| SerpApi date grid | 5 (+1 wasted, see F6) | 5 new quasi-random date pairs |
| Google Flights scraper | 4 | re-priced the 4 pairs already seen ≤ €600 |
| Aviasales cache | 2 | nothing usable (F6) |
| SearchAPI rectangle sweep | 0 | dropped: not enough credits (F1) |

## Findings, most important first

**F1. The full-coverage sweep is out of credits and fails silently.** SearchAPI gave
100 lifetime credits. Three sweeps (17 Jul, 1 Aug, 15 Aug; 28 calls and ~3,561 pairs
each) spent 84. 16 remain, 12 after the 4-credit margin; the current window needs 26.
The gate is all-or-nothing (`_sweep_affordable`, `run_batch.py`), so the 12 are
stranded. Every Saturday the log says `sweep unaffordable (12 available < 26 planned)`
and nothing else reports it.

**F2. Those sweeps covered a window that no longer exists.** They priced departures
12 Sep – 16 Nov. The window moved on 6 Oct and again on 7 Nov. The current window has
never been swept, and nothing in the engine treats a window change as "start over".

**F3. The fallback sampler is tiny while free capacity sits idle.**
- SerpApi: 250 searches/month, 225 usable. The tracker used 78 in the last 30 days
  (203 available on 11 Nov). The grid is capped at 5 per scan (`SERPAPI_DISCOVERY_CAP`).
- Scraper: capped at 25 per scan and proven from GitHub Actions at that rate (350 ok /
  0 empty in Aug, 314 / 18 in Sep) — but that was the one-way test search, paused on
  5 Oct. For this search it verified 0 pairs in Aug, 2 in Sep, 3 in Oct, 4 in Nov,
  because it is only fed pairs already seen ≤ €600 (`select_candidates`).
- Planner comments still say the scraper is "captcha-walled from CI" (true on 14 Jul);
  the ledger shows ~95%+ success since.

**F4. A new cheapest price does not reach the phone.** Alerts are per (source,
itinerary) and need an earlier observation by the same source (`lib/alerts.py`); the
push fires only on alerts (`scripts/notify_ntfy.py`). The current best, 557 EUR
(MAD 28 Jan → 20 Apr), was found by the grid on 11 Nov: 0 alerts, no push. Only 2 pairs
in the window have been priced twice by the same source. Last alert: 30 Sep.

**F5. The site can show a stale low over a fresher, higher price.**
`getTopAlternatives` and `getHeatmapGrid` take the latest row per source and then the
minimum across sources. BCN 12 Jan → 13 Mar shows 589 (SerpApi, 9 Nov) although the
scraper saw 630 on 11 Nov; BCN 14 Jan → 15 Mar shows 589 (24 Oct) vs 630 (11 Nov).
SerpApi and the scraper read the same Google data, so the latest live observation
should win. `select_candidates` already does this.

**F6. Two rails spend calls and return nothing for this search.**
- Aviasales has 0 rows, ever, inside the current window (its cache holds 3–56 day stays).
- The OTA seller check spends one SerpApi search per scan and cannot complete on a
  round trip: `top_booking_token` looks for `booking_token`, but round-trip responses
  carry `departure_token` (`tests/fixtures/serpapi_flights_response.json`: 3 vs 0).
  `booking_options` calls: 8, 14, 13 in Jul–Sep (one-way search active), 1 in Oct, 0 in Nov.

**F7. /ops confidence says "medium ~89%".** It counts source families (google +
cached), not how much of the window has been priced. With 0.5% priced it overstates.

## What the old sweeps say about how hard the problem is

Analysed the three full sweeps (old window, ~1,780 pairs per origin each):

- **The cheapest price was a plateau, not a needle.** Min 555–565 EUR; 5–12% of all
  pairs were within 2% of it.
- **Structure:** dep-day + return-day effects explain 87–95% of price variance; the
  return date alone 61–85%, the departure date alone 22–41%.
- **Stability:** 1 → 15 Aug, prices correlated 0.90–0.98 with a median change of
  4–7 EUR and the minimum moved by ≤ 2 EUR. 17 Jul → 1 Aug was looser (0.70–0.88).
- **Sampling:** N well-spread random pairs per origin landed within 2% of the true
  minimum in 72–86% of trials at N=20, 88–98% at N=40, ~100% at N=80.

So 557 EUR is plausibly near the floor (11 MAD and ~6 independent BCN samples), but
not shown to be. This window also holds the Christmas departure peak (the five late-Dec
/ early-Jan samples are 739–1,012 EUR), so the old shape may not transfer. A plateau
also means the useful output is the map of which dates sit on the floor — which 21
points cannot give.

## Recommended order

1. **Price the whole window with SerpApi's calendar engine.** SerpApi documents
   `engine=google_flights_calendar` (`date_range_start`, `date_range_end`,
   `round_trip_days`, `fixed_outbound_date`; response `prices[]` with `outbound_date`,
   `return_date`, `price`). One call per stay length = 31 × 2 origins = 62 searches for
   the full map, against 203 free and renewing. **Unverified:** free-plan access, the
   longest date range and trip length one call accepts, and the per-call cost. Per the
   ledger rule it needs a metered method, pool maths and a real-response fixture before
   the first call — the first call is the probe.
2. **Push on a new window low:** when a scan stores a price below every earlier price
   in the current window (and under the watch bar), alert once.
3. **Fix F5** in the two web queries (latest live observation across sources).
4. **Stop the dead spend (F6):** skip the OTA call on round trips or implement the
   departure-token step; skip Aviasales when the stay range is outside its cache.
5. **Between sweeps, use the idle scraper** (up to 25 per scan) to re-price floor pairs
   and fill gaps, instead of only pairs already ≤ €600.
6. **On a window change, bootstrap:** treat a new window as unpriced and sweep it first.
7. **Decide the last 12 SearchAPI credits:** one-origin sweep of this window now (MAD
   needs 13 windows, so one window or the margin has to give), or keep as reserve. A
   paid SearchAPI plan is Carlos's call; third-party listings say from $40/month, not
   verified on the provider's page.
8. Make confidence include date coverage, and align the landing claim with what runs.

## Method

DB queries on `calendar_snapshots`, `point_queries`, `spend_events`, `scan_runs`,
`ledger_runs`, `searches`; Actions log of run 42369704867 (7 Nov); code read of
`run_batch.py`, `lib/{runner,planner,sweep,followup,alerts,sources,scanops}.py` and the
three source clients. Surface statistics and the sampling simulation were computed from
the `searchapi` rows of 17 Jul, 1 Aug and 15 Aug (400 trials per budget).
