# Passive Vuelazo collector + reliable Nairobi tracker — implementation notes (2026-11-14)

Plan: `docs/notes/PLAN-passive-vuelazo-reliable-nbo-2026-11-14.md`. Product amendment:
`docs/DECISIONS.md` "Amendment (14 Nov 2026)". This note records the implementation
choices, the real runs with their ledger receipts, and the budget.

Commits on `main` (none pushed yet):

| Commit | Workstream |
|---|---|
| e8567bd | A1–A4: collect-only mode, 6 Explore calls/day, daily `deals.yml`, loud failure (plan file committed here too) |
| 24c15fb | A5: dead-man watchdog (`lib/watchdog.py`, `scripts/watchdog.py`, `watchdog.yml`) |
| 4c585dd | B1–B4: rolling Nairobi window, stays 30–90 every 7 days, 93-day calendar strips |
| 9f374ff | B5: audit fixes (catch-up, verification lag, F1, F5, F6, F7) |

**Nothing takes effect in CI until it is pushed**, and the production `searches` row
still has the old window until B6 runs (needs Carlos's yes; diff below).

## Workstream A — passive collector

**A1. `run_deals.py --collect-only`** (a flag, not a yaml `mode`: the workflow chooses
the mode per run, and a manual `full` dispatch must stay possible for the future paid
trial). Same code path as the full pipeline, with branches:
- only the Google Explore client is built; the Anthropic/Telegram/Resend rails
  (`_build_service_rails`) are not built at all, so they cannot be called;
- no Aviasales sweep, watchlist, gate, verification, drafting, publishing or free picks;
- the cost vector has one line: `serpapi_vz` explore.
- The flag is mutually exclusive with `--queue`, `--publish-only`, `--auto-approve`
  and `--skip-publish` (argparse error, not a silent override).

**Anchor fix (found on the first real run).** `_ensure_fare_anchor` used to skip the
free `/account` probe when an anchor existed. The `serpapi_vz` anchor was from 9 Aug
and said 156 left; the real figure was 244. It now probes on every run for a dedicated
key (free call, not metered), records a `quota_snapshots` row with
`plan_renewal_date`, and re-anchors. A failed probe keeps the last anchor. The old test
"does not re-probe" was reversed on purpose.

**A2. `explore.calls_per_day: 6`** (`routes/vuelazo.yaml`). 6 × 31 = 186 per month,
inside 225 usable (250 − margin 25), with 39 to spare for retries and a manual `full`
run. The 120 windows are each re-priced about every 20 days. `windows_due_today`
plans the full day's rotation, then drops the calls already made today, so a re-run
the same day continues the rotation instead of repeating it. **No change to what an
Explore window means** (one origin, area and month per call): I did not find a cheaper
way to get more points per route from the same endpoint, so there is nothing to
propose.

**A3. `deals.yml`.** Daily at `41 3 * * *` (away from scan 05:23 and watchdog 19:37).
The `workflow_dispatch`-only guard is gone. There is a new dispatch input `mode`:
`collect` (the default, and what cron runs) or `full`. `publish_only` still works.
The job summary prints the mode and any error.

**A4. Loud failure.** `collect_verdict`:
- every call failed, or zero observations stored → `error`: status `failed`,
  exit 1, the existing failure push fires and carries the summary error;
- some calls failed → `warning` in the summary, no page.

A crash also writes a redacted `summary["error"]`. stdout and stderr are reconfigured
with `errors="replace"`: the first real run died on a cp1252 console before its first
call.

**A5. Watchdog.**
- Runs daily at `37 19 * * *`, reads Turso only and makes no metered calls.
- Checks:
  - an Explore observation stored in the last 48 h;
  - a successful Nairobi scan in the last 4 days;
  - the last two calendar sweeps both erroring within 30 days;
  - each SerpApi pool's run-out date.
- The pool check projects at the **28-day** burn rate against `plan_renewal_date`. A
  7-day window counted one 62-call sweep as a daily rate and raised a false "runs out
  24 Nov" alarm.
- Pushes:
  - when a check turns bad, then every 3 days while it stays bad;
  - one low-priority note on recovery.
  - State is kept in a `watchdog_state` table.
- `--dry-run` writes nothing and sends nothing.
- **Note for Carlos:** the `watchdog_state` table already exists on production Turso.
  Before the test isolation was fixed, a test leaked to Turso through `load_dotenv`,
  read only, but ran its `CREATE TABLE IF NOT EXISTS`. It is empty and the first real
  run would create it anyway. The test now blanks `TURSO_*` and stubs `load_dotenv`.

**A6. Weekly Monday push: drafted, NOT shipped.** See "Waiting on Carlos" below.

## Workstream B — Nairobi

**B1. Rolling window.**
- `RollingWindow` in `lib/config.py`: departures from `start_months` after the
  window's step date, for `span_months`, never before `not_before` (2026-12-27).
- The step date is today rounded down to a 7-day step counted from Monday
  2024-01-01. The window moves on Mondays, so strips stay fresh between steps.
- Month arithmetic clamps to the month end (31 Jan + 1 month = 28 Feb).
- `latest_return` = last departure + max stay. There is **no end date**.
- `load_route` and `route_from_json` resolve the window on every load. `route_store`
  already rewrites `config_json` when the resolved dates change, so the `searches` row
  and the web radar read the same dates. The radar prints the rule and "dates shown are
  as of the last scan".

**B2. Sampled stays.**
- `StayPreferences.step_days` and `sampled()` give 30, 37, …, 86, 90: always both
  ends, so 10 lengths.
- Every count uses the sampled set: the sweep planner, the discovery grid, candidate
  selection, `window_coverage` and the web (`web/src/lib/stays.ts`).
- Python↔TS drift guard: `scripts/gen_stays_fixture.py` + `web/scripts/check-stays.mjs`
  in `tests.yml`, the same pattern as the estimator.

**B3. Probe: one credit, a 93-day range comes back whole.** Run b348e836006f,
MAD→NBO, 30-day stay, departures 27 Dec → 29 Mar: 93 of 93 departures priced.
Receipt: `serpapi reserved 1 used 1`. Fixture: `tests/fixtures/serpapi_calendar_93day.json`.
`SERPAPI_CALENDAR_MAX_DEPARTURE_DAYS` is now 93. That is the proven length: a
three-month window is at most 92 days, so it fits.

**B4. Refresh every 7 days** (`SERPAPI_SWEEP_REFRESH_DAYS`, was 14). A sweep is 2
origins × 10 stays = 20 strips, each one call now that a 93-day range is one strip.
That is cheap enough to re-price every week, and it matches the
window step. The rule that a sweep shrinks or waits rather than starve the regular
scans is unchanged (`_serpapi_sweep_allowance`).

### Budget (SerpApi, monthly pools of 250, 25 margin → 225 usable)

| Pool | Use | Calls/month | Notes |
|---|---|---|---|
| `serpapi` | regular Nairobi scans (Mon/Wed/Sat) | 78–91 | ~6 per scan; no OTA reserve on round trips since F6 |
| `serpapi` | calendar sweep, 20 strips weekly | ~87 | 20 × 31/7 |
| `serpapi` | **total** | **~165–178 of 225** | ~47–60 spare for retries and the `/ops` probe |
| `serpapi_vz` | Explore collector, 6/day | 186 of 225 | 39 spare |
| `searchapi` | lifetime | 0 | 12 usable, untouched (F1) |
| `googleflights` | scraper verification | free | politeness cap only |

The 28-day burn rate can be measured by the watchdog. Right now the `serpapi` pool
has 129 usable left until it renews on 6 Dec. The burn rate is 5.3 calls/day, which
needs about 116 of them, so this month is tight. If the pool runs short, the sweep
shrinks first. `serpapi_vz` has 244 left and renews on 9 Dec.

**B5. Audit fixes** (9f374ff):
- **Crash catch-up.** `alerts.catch_up_since` sets `since` to the end of the
  search's last ok or degraded scan, bounded to 14 days back. Rows that a dying run
  stored count as "this scan's" for the next run. Old rows cannot fire twice: they
  are before that finish time, and `prior_alerts` and the per-(source, itinerary)
  dedup mute anything already alerted. `execute_search` also runs `evaluate()` in a
  `finally`, so a raising step still evaluates what it stored. The step's own
  exception is never masked.
- **Verification lag.** `RunPlan.verify_after_sweep` reserves `followup_cap − planned
  candidates` extra scraper slots when a sweep is planned (round trip, googleflights
  verifier). After the sweep stores cells, the runner calls `select_candidates`
  again. Those slots are reserved in the quote, so spend stays an upper bound. They
  are free calls.
- **F1.** The SearchAPI rectangle sweep is manual only (`scan.yml` `force_sweep`).
  The Saturday and cooldown gate is deleted.
- **F5.** The web joins one current observation per trip (`currentObsJoin` in
  `web/src/lib/queries.ts`): inside the freshness window, a live fare beats a lead,
  then the newest wins. This is the same rule as `select_candidates`. Checked on
  Turso: BCN 12 Jan → 13 Mar now shows the scraper's 630, not the stale SerpApi 589.
- **F6.** No OTA-seller call and no reserve on round trips. A round-trip response
  carries `departure_token`, not `booking_token`, so the call could never complete.
  The departure-token step would cost a second call per check, and on 30–90-day
  stays the OTA delta is a lead anyway. **Aviasales, measured in the B6 scan:**
  2 calls (free), 2 rows: MAD 56-day stay at 547 EUR (in range) and one 7-day stay
  (out of range, ignored). It returns in-range leads again, so it stays on.
- **F7.** `with_coverage` caps the confidence level by how much of the window is
  priced in the last 21 days: below 25% it is capped at low, below 75% at medium. The
  note says, for example, "20 of 4,030 date pairs priced in the last 21 days (0%),
  capped at low". Drop pushes compare `family_level`, so a window step never pages as
  a "confidence drop".
- The stale "captcha-walled from CI" comments are rewritten. The scraper has run
  ~95% ok from Actions since July.

**B6. Production write (applied 2026-11-14 on Carlos's yes).** Dry-run diff of the live `searches` row
on Turso, read with `.env` loaded:
- window: `latest_departure` 2027-03-01 → 2027-03-09; `latest_return` 2027-05-30 →
  2027-06-07; `rolling` block added;
- stays: `min_days` 60 → 30; `step_days: 7` added.

Caveat: `scripts/apply_route.py --dry-run` does not load `.env`. Its "DB" line shows
the local SQLite, not Turso. Run it with the `TURSO_*` vars set, or use the
`apply-route` workflow.

## Real runs and receipts

| Run | What | Receipt |
|---|---|---|
| daf12d2ebaa8 | first collect-only try; died on the cp1252 console before any call | `serpapi_vz reserved 6 used 0` (status failed) |
| 72b6e003a152 | collect-only: MAD/BCN/VLC/ALC south_america 2027-03, MAD/BCN asia 2027-03; 113 rows | `serpapi_vz reserved 6 used 6` |
| b348e836006f | B3 probe, 93-day strip | `serpapi reserved 1 used 1` |
| 3e8758efb2f2 | B6: real scan + sweep on the new window (local, against Turso); 1,546 rows, 0 alerts, ok | `serpapi reserved 25 used 25` (20 calendar + 5 grid) · `googleflights reserved 25 used 25` (free) · `aviasales reserved 2 used 2` (free) · no SearchAPI spend |

B6 run details:
- The sweep covered 20 of 20 strips, 73 departures each (27 Dec → 9 Mar), 1,460
  cells, 0 empty, 0 errors. Coverage after the run: "1,460 of 1,460 date pairs priced
  in the last 21 days (100%)". Confidence: medium (one live family, google).
- Verification after the sweep: candidate selection ran again after the sweep and
  went from 5 to 25 (the cap). The scraper verified 25 trips and stored 23 fares; 2
  came back with no flights. Its log line still said "followup searchapi" from an old
  hard-coded label; it now names the real source.
- Best verified fare in the new window this run: 600 EUR, BCN 16 Jan → 15 Feb (30 d,
  SerpApi). Cheapest calendar cells (leads) by stay: 30–44 d 594 EUR · 45–74 d 589 EUR ·
  75–90 d 594 EUR, all from BCN. No alert fired: the window's bar is still the 557
  from 11 Nov, which is correct.
- The live `routes` row now reads `latest_departure 2027-03-09`, `latest_return
  2027-06-07`, plus the `rolling` block. The web radar reads these dates from that row.
- **The watchdog read the new window and flags the `serpapi` pool**, dry run right
  after the scan: 103 usable, a 28-day rate of 6.2/day, about 137 needed before the
  6 Dec renewal, run-out around 30 Nov. The rate includes today's one-off 62-strip
  sweep plus this 20-strip sweep and the probe. The forward rate is about 35 a week
  (a 20-strip sweep plus 3 scans × 5), about 110 to renewal, so it is still short by
  about 7. The sweep allowance keeps scan headroom, so next week's sweep shrinks before
  any scan is skipped. The first real watchdog run (19:37 UTC) will send this push.
  Repeats come every 3 days while the pool stays short.

Forced-stale watchdog demo (dry run, `--now 2026-11-20T19:37Z`):
`out/watchdog_forced_stale_2026-11-20.txt`. It produced the collector and scan pushes
and sent nothing.

## Waiting on Carlos

1. ~~Push~~ done 2026-11-14 (64ee520..ff6b024; `tests` green on GitHub).
2. ~~B6~~ done 2026-11-14 (run 3e8758efb2f2 above). Open: the `serpapi` pool is
   short until 6 Dec. Let the sweep shrink (the default), or pause the sweep until
   renewal?
3. **A6 weekly push.** Does he want it, and is this wording right? Draft (owner push,
   English like the other ops pushes; sent Monday ~08:00 Madrid):

   > **Weekly status: 9–15 Nov**
   > Vuelazo collector: 7 of 7 days ran, 790 fares stored.
   > Baselines: 31 of 186 routes have 4+ observations.
   > Nairobi: 3 of 3 scans ok; calendar sweep 20 of 20 strips.
   > Best verified: 549 € MAD 3 Feb → 14 Apr (−8 € vs last week).
   > Cheapest by stay: ~1 month 480 € (30 d) · ~2 months 549 € (70 d) · ~3 months 612 € (86 d).
   > Window: departures 27 Dec – 9 Mar (as of the last scan).

   (Numbers are illustrative.) Questions that go with it:
   - the stay buckets: 30–44, 45–74 and 75–90 days?
   - "enough history" = the route's `min_observations` (4)?
   - should a quiet week still send? (Yes is the point: silence is the signal.)
4. Untouched as instructed: the landing-page scan-frequency claim and the 12 SearchAPI
   credits.
