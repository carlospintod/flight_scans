# SerpApi calendar sweep — 2026-11-14

Audit recommendation #1 (`docs/notes/audits/nbo-search-2026-11-14.md`): price
the whole Nairobi window instead of 5 date pairs per scan.

## What it does

- One `google_flights_calendar` call prices ONE stay length (a "strip") for every
  departure day of a range. The window (27 Dec – 1 Mar, stays 60–90, 2 origins) is
  31 × 2 = **62 calls for all 4,030 date pairs**.
- It runs inside the normal batch, for the owner search only, at most once per run.
  Each strip is re-priced every **14 days** (`SERPAPI_SWEEP_REFRESH_DAYS`).
  Between refreshes, scans plan zero strips and spend nothing extra, except to
  re-ask a strip that came back empty (choice 4).
- Cells are stored in `calendar_snapshots` with source `serpapi_calendar`. Every
  call is logged in the new `calendar_sweeps` table (what was asked, how it landed).
- Ledger: metered as `serpapi.calendar` = 1 unit, charged before each call by the
  `GuardedClient`. It is quoted in the search's single `serpapi` cost line and
  reserved all-or-nothing like every other call.

## Probe (the first call)

One call through the ledger: MAD→NBO, 90-day stay, 27 Dec – 1 Mar. Receipt: spend
event 6907, `serpapi`/`calendar`, 1 unit, ok.

| Question | Answer |
|---|---|
| Free plan allowed? | Yes |
| Real cost | 1 search (`/account` 229 → 228; usage 21 → 22) |
| Range per call | All 65 requested departure days came back |
| Trip length | 90 days accepted |
| Latency | 4.6 s |
| Cell shape | `{outbound_date, return_date, price, low_price}`, no gaps |

The response is the test fixture `tests/fixtures/serpapi_calendar_response.json`,
with search ids and URLs replaced.

## Implementation choices

1. **One call per stay length, not per departure day.** `round_trip_days` strips
   cost 62 calls. `fixed_outbound_date` (one departure, every return) would cost 130.
2. **Separate row label, same pool.** The ledger source stays `serpapi` (same
   account). Rows are labelled `serpapi_calendar` because a cell is Google's grid
   price, not a priced itinerary: it has no carrier detail and the scraper still has
   to verify it.
3. **Sweep cells never alert** (`lib/alerts.py` `NOMINATION_ONLY_SOURCES`). Alerts
   compare each source with its own history. Two sweeps two weeks apart would fire a
   `new_low` for every one of ~4,000 pairs that slipped 1% under €600, which is the
   2026-07-05 flood at sweep scale. Cells nominate: `select_candidates` already
   sends the cheapest to the free scraper, and the scraper's rows alert as before.
4. **A call log, not freshness guessed from prices.** A strip is due when no
   call in the last 14 days covers its whole departure range with a usable answer.
   `ok` counts, `error` never does, and `empty` counts only when the previous call
   for the same origin, destination and stay was also empty. That rule handles:
   - a sweep cut short by budget or time resumes on the next run;
   - a failed strip is retried;
   - an empty strip is asked once more on the next run, then believed for 14 days,
     so a one-off "no results" doesn't leave a two-week hole and a truly empty
     strip isn't re-bought every scan (added after the real run);
   - a moved window counts as unswept (audit F2/#6).
5. **Spare capacity only.** The batch grants
   `available − 7 (this scan) − 6 scans × 7 × active searches`, capped at 80 strips.
   With 203 available that is the full sweep. When the pool is short, the sweep
   shrinks or waits for the monthly reset. The regular scans never go short because
   of it.
6. **One `serpapi` cost line.** `settle()` gives each primary line the source's
   whole spend up to that line's size. Two primary `serpapi` lines would count
   `used_units` twice, so the sweep is folded into the one line and its note shows
   the breakdown.
7. **Order:** never-swept strips first, then the stalest. Ties are spread across
   stay lengths (van der Corput) and alternate origins, so a partial sweep still
   samples the whole window.
8. **Bounds:**
   - 60 s timeout per call;
   - 15 min wall budget per sweep, which keeps the CI job's 50 min safe. The real
     run took 10 min for 62 strips, so the batch's time estimate uses 15 s per
     strip (about the p90), up from the probe-based 8 s;
   - stop after 3 errors in a row (a dead key costs 3 charged calls, not 62);
   - cells outside the current window are dropped.
9. **Departure range per call is capped at 65 days**, because that is what the
   probe proved. Wider windows are chunked rather than risk a silently truncated
   strip.
10. **`low_price` is stored as `is_lowest_price`**, Google's own cheap-cell flag. It
    is looser than SearchAPI's: 33 of 65 cells in the probe, against 8% for SearchAPI.
    Only the legacy candidate mode reads it, and that mode is off for this search.
11. **`gl` is left at SerpApi's default (`us`)**, the same as the existing point
    queries, so prices stay comparable across rails. Spain as point of sale is
    untested.
12. **Grid cells are leads, like Aviasales quotes** (`lib/followup.py`
    `LEAD_SOURCES`, added after the real run). Two places assumed every non-Aviasales
    row was a live fare:
    - **The OTA seller check** enriches the scan's cheapest fresh fare. In the run
      it spent its call on a €594 grid cell. It now skips leads.
    - **Candidate selection** takes an itinerary's newest row as its current price
      for the `drop_above` filter. A fresh grid cell could have pushed aside a
      verified price. Leads now rank below live rows, exactly as Aviasales does.
    Grid-only itineraries still nominate, which is the whole point.
13. **Ledger close-out race, fixed** (`QuotaLedger.capture_anchors_from_snapshots`,
    found in the real run). The close-out promotes quota snapshots written during
    the run. A 2026-07-17 fix kept out the run-start `/account` snapshot with a
    timestamp cutoff, but timestamps are truncated to the second. Here the
    snapshot and the cutoff shared a second (00:54:47Z), and the start balance
    (228) was re-anchored after the run's 68 spend events. The ledger then showed
    203 usable while SerpApi had 161 left. CI had done the same to searchapi's
    start balance at almost every close-out since at least 21 Oct. That was
    harmless only because those runs spent no searchapi credits; on a
    rectangle-sweep day it would have hidden ~28 lifetime credits until the next
    run's `/me` check. The cutoff is
    now a `quota_snapshots` row id (`snapshot_watermark()`), the same
    order-not-clock rule the spend watermark already follows. The stale anchor
    was replaced the same night by a fresh `/account` anchor (161), the step
    every run takes at start.

## Budget (free plan, 250/month, 25 margin)

| Use | Searches/month |
|---|---|
| Regular scans (grid 5 + OTA 2, ~13 runs) | ~91 |
| Sweeps (62, every 14 days) | ~124 |
| **Total** | **~215 of 225 usable** |

It is tight but self-regulating: the headroom rule defers or shrinks a sweep
rather than starve the scans.

This month, after the first sweep: 161 left, 136 usable. Six regular scans and
the empty-strip retry before 28 Nov leave about 93. The allowance then is
93 − 7 − 42 = 44, so the 28 Nov refresh prices 44 of the 62 strips, stalest
first and spread across stays. The other 18 come due after the monthly reset.
The free plan did not bill the "no results" call, so a full sweep costs 61–62.

## Not done here (next steps)

- **No push yet for a sweep-found low.** Cells don't alert (choice 3), and the
  scraper's first look at a new pair has no prior row to compare with (audit F4).
  The next step is audit #2: push once when a verified price beats the window's
  previous best.
- **Verification lags one scan.** Candidates are chosen at plan time, before the
  sweep runs, so the scraper verifies sweep-found pairs on the next scan (≤ 3 days).
  After the real run, the next scan's 25 slots go to the €557 pair plus 24 €594
  cells spread over Jan–Mar.
- **F5:** web queries still take the minimum across sources' latest rows, so a
  calendar cell can show beside a newer, higher scraper price for the same pair.
- **Capacity view** (`web/src/lib/capacity.ts`) does not count the owner sweep's
  monthly load. Only one search is active today.
- **SearchAPI rectangle sweep** (12 usable lifetime credits) is now redundant.
  Whether to retire it or spend it is Carlos's decision (audit #7).
- **Landing claim** ("thousands of date pairs, three times a week"): the sweep is
  every 2 weeks. The copy is Carlos's call.
- **Open question:** does a range longer than 65 days come back whole? It is
  untested and not needed for this window.
- **Heatmap colours at full coverage.** The BCN grid now shows its 222 €594 cells
  in red: the legend's scale runs "cheaper → pricier" over 589–594 EUR, so a good
  fare sits at the red end. The legend explains it, but it reads as alarming.
  Unchanged here; it's a design call.
- **SerpApi key copies in `quota_snapshots.raw_json`** (pre-existing, found
  here). `run_batch` stores the raw `/account` payload, which includes `api_key`
  and the account email. All 55 serpapi snapshots carry them. The web reads only
  a reset date from that column, server-side, so nothing reaches a browser. It is
  still a needless copy of a secret. Fixing it means storing only the safe fields
  and scrubbing the existing rows, a production write that needs Carlos's OK.
  **Fixed 2026-11-14:** `check_quota()` keeps only an allowlist
  (`ACCOUNT_SAFE_FIELDS`, `tests/test_quota_secrets.py`), and with Carlos's OK
  `scripts/scrub_quota_secrets.py --apply` cut the 55 old rows to it. Afterwards
  no live table held a key, email or account id.
- **Secrets in network-error messages.** requests puts the request URL into
  its exception text, so a refused connection carried SerpApi's `api_key` into
  logs, `ledger_runs.summary_json` and the "rail caído" push (CI masks secrets
  in its own log only). Aviasales' `token` param, the Telegram bot token (in the
  URL path) and the ntfy topic rode the same way. **Fixed 2026-11-14:**
  `serpapi_io`, `explore_api`, `aviasales_api`, `telegram_api` and `pushes` wrap
  these errors with `lib/credentials.redact_network_error` (raised `from None`,
  so no traceback prints the original), and `scripts/notify_ntfy.py` cuts the
  topic inline; `tests/test_error_redaction.py`. Still open:
  `scripts/setup_telegram_webhook.py` has no try/except, so a network failure
  there prints the bot token in a traceback to the terminal that ran it.

## Real run

Batch `05f754d54ac1`, 2026-11-14 00:54:44–01:06:01 UTC, local trigger,
`--sources serpapi` (the full batch path, SerpApi only). Status `ok`: 1 of 1
searches ran, 0 skipped, 0 alerts.

**Plan and reservation.** The pool anchored at 228 via `/account`. The allowance
was min(80, 203 − 7 − 42) = 80, so all 62 due strips were planned: "calendar
sweep: 62 of 62 due strips this run (~4030 date pairs)". One `serpapi` cost line
of 62 strips + 5 grid points + 2 OTA = 69 units was reserved all-or-nothing.

**Ledger receipts** (`spend_events`, run `05f754d54ac1`):

| Op | Result | Units | Events |
|---|---|---|---|
| calendar | ok | 61 | 6908–6969 |
| calendar | empty | 1 | 6949 |
| point_query (grid 5 + OTA 1) | ok | 6 | 6970–6975 |
| **Used / reserved** | | **68 / 69** | `run_reservations`: consumed |

Every row was written before its call: mid-run, the call in flight showed as
`pending` (event 6933). SerpApi's own counter went 228 → 161, so 67 searches were
billed. The "no results" call was free, which leaves the ledger one unit high:
the safe direction.

**Sweep.** 62 calls, 0 errors, 600 s of calls (mean 9.7 s, median 9.2 s, p90
15.1 s, max 21.5 s), well inside the 900 s budget. 61 strips returned all 65
departure days. BCN→NBO with a 65-day stay came back "Google Flights Calendar
hasn't returned any results for this query" in 5 s. The MAD 65-day strip just
before it returned 65 cells, as did BCN 64 and 66. Under choice 4 as amended it
is the only strip due on the next run: checked against the live DB, 1 due today,
62 on 28 Nov with BCN 65 first.

**Coverage.** 3,965 `serpapi_calendar` rows = 3,965 distinct date pairs in one
snapshot. The radar reads "3,965 of 4,030 date pairs priced in the last 21 days":
MAD 2,015 of 2,015, BCN 1,950 of 2,015 (the 65-day row is dark). Before the
sweep, a scan stored about 26 rows.

**Prices.** The cheapest cells are €594 (222 cells, all BCN, departures 19 Jan –
1 Mar), then €614 (334), €617 (320) and €619 (95). The sweep found nothing below
the €557 a point query saw on 11 Nov (MAD 28 Jan → 20 Apr, Saudia). The calendar
prices that pair at €617.

**Grid cell vs point query, same trips ten minutes apart** (this run's 5 grid
points):

| Trip | Point query | Calendar cell |
|---|---|---|
| MAD 1 Jan → 1 Apr | 785 | 785 |
| BCN 13 Jan → 14 Mar | 621 | 621 |
| BCN 8 Feb → 24 Apr | 625 | 621 |
| MAD 26 Jan → 4 Apr | 758 | 627 |
| MAD 21 Feb → 14 May | 643 | 714 |

Cells are good nominations and poor prices, which is why they never alert, never
displace a verified price and never get the OTA check (choices 3 and 12).

**Alerts.** The alert pass read 9,314 latest rows and fired nothing. Calendar
cells are skipped, and this was their first snapshot anyway.

**What the run changed** (all offline-tested, 425 tests green):
- empty strips are re-asked once (choice 4);
- grid cells are leads for the OTA check and current price (choice 12);
- the ledger close-out race is fixed and the stale anchor replaced (choice 13);
- the batch time estimate is 15 s per strip (choice 8).
