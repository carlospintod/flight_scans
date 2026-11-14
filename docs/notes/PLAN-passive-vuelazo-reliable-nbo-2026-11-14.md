# Plan — passive Vuelazo collector + reliable Nairobi tracker (2026-11-14)

Written for the session that executes it. Read `CLAUDE.md` first; its non-negotiables
apply in full (ledger before every metered call, offline tests, no secrets in code).

## Why this plan exists (Carlos's decisions, 2026-11-14)

1. **Vuelazo goes passive.** A deal engine needs live Google discovery at volume
   (SearchAPI Developer, about $40/month). Carlos is not paying for that now. Until he
   does, Vuelazo only collects live price history, silently, so a later paid trial
   starts with baselines. No nomination, verification, drafting, approval or publishing.
   This is a deliberate detour from the M0→M4b milestone order; record it in
   `docs/DECISIONS.md` as an amendment dated today.
2. **Silent failure is the main risk.** Carlos must get a push if collection stops, so
   nobody finds two days of data three months from now.
3. **Nairobi is the active mission.** Carlos wants to fly in the first half of 2027 for
   one, two or three months. The tracker must show how prices move and which date
   combinations are cheap, and run reliably from now on.
4. **Everything stays on free tiers.** Buying any plan is Carlos's explicit action.

Carlos's answers to the three open choices:

| Choice | Answer |
|---|---|
| Nairobi departure window | **Rolling**: "three months, from next month". Today that is 14 Dec → 14 Mar, with a hard floor of **27 Dec 2026** (no travel before Christmas). He chose this over a fixed Jan–Jun window so calls are not spread thin. |
| Nairobi stay lengths | **30–90 days in weekly steps** (about 10 stay lengths), every departure day. |
| Vuelazo collector data | **Google Explore only.** The cached Aviasales sweep stays off. |

## Facts established today (do not re-derive)

- Scheduled today: only `scan.yml` (Mon/Wed/Sat, cron 05:23 UTC; GitHub actually
  starts it 7–9 hours late, e.g. 12:18 and 14:02 UTC). `deals.yml` has its schedule
  but the job is guarded by `if: github.event_name == 'workflow_dispatch'`.
- Secrets present in GitHub: `SERPAPI_KEY` and a separate `SERPAPI_KEY_VZ`, plus
  `NTFY_TOPIC`, `TURSO_*`, `TRAVELPAYOUTS_TOKEN`, `ANTHROPIC_API_KEY`,
  `RESEND_API_KEY`, `RAPIDAPI_KEY`. Not checked: that the `_VZ` key is still valid or
  what balance it has.
- Ledger pools: `serpapi` and `serpapi_vz` are each monthly 250, margin 25
  (`lib/sources.py`). Explore in August ran 4 calls/day on `serpapi_vz`, each call
  returning about 66 destinations for one (origin, area, month) window; the full grid
  is 4 origins × 5 areas × 6 months = 120 windows (`lib/explore_api.py:201`,
  `routes/vuelazo.yaml` `explore:`).
- `run_deals.py` runs Explore once a day inside the full pipeline
  (`run_deals.py:328-351`, `639-717`); there is no collect-only mode.
- The SerpApi calendar sweep prices one stay length per call for up to 65 departure
  days (`SERPAPI_CALENDAR_MAX_DEPARTURE_DAYS`, `lib/sweep.py:530`), refreshes every 14
  days, and treats a moved window as unswept
  (`docs/notes/serpapi-calendar-sweep-2026-11-14.md`, choice 4).
- `window_best` pushes exist since today (`docs/notes/window-best-alert-2026-11-14.md`).
- Failure pushes exist for a failed job or a missing summary
  (`scripts/notify_ntfy.py`, `deals.yml` "Notify failure"), and source-health pushes
  for the scan (`lib/health.py`). **Nothing detects a workflow that simply stops
  running**, or a collector that runs "ok" while storing nothing.
- Stored data and the August evidence are summarised in
  `docs/notes/viability-2026-08-13.md` and `docs/notes/audits/nbo-search-2026-11-14.md`.

## Workstream A — Vuelazo passive collector (do first: every day adds history)

**A1. Collect-only mode.** Add a mode to `run_deals.py` (flag or `routes/vuelazo.yaml`
`mode: passive`, your choice) that runs only: bootstrap, ledger anchor for
`serpapi_vz`, today's Explore rotation through the `GuardedClient`, persistence to
`fare_observations`, receipt, summary. It must not call the Aviasales sweep, the
watchlist refresh, the gate, verification, Anthropic, Telegram, Resend or ntfy publish.
Evolve the existing code path; do not fork a second pipeline. Keep the member-lifecycle
step in the workflow as it is.

**A2. Use the pool for collection.** With no verification spend, raise
`explore.calls_per_day` so a month stays inside 225 usable with headroom for retries
(6/day = 186/month is the suggested value; confirm against the ledger's pool maths).
At 6/day each of the 120 windows is repriced about every 20 days. If you see a cheaper
way to get more frequent per-route points from the same endpoint, propose it to Carlos
before changing what a window means.

**A3. Re-enable the schedule, once a day.** Replace the 3×/day cron with one daily run
in collect-only mode, remove the `workflow_dispatch`-only guard for that mode, and
rewrite the pause comment to say what runs now and why. `--publish-only` dispatch must
keep working.

**A4. A run that collects nothing must fail loudly.** If every Explore call fails, or
the run stores zero Explore observations, the summary status is an error and the job
goes red so the existing failure push fires. Partial failure is a warning in the
summary, not a page.

**A5. Dead-man watchdog (covers both projects).** New small scheduled workflow plus
script, daily, at a time away from the other two. It reads Turso only (no metered
calls) and pushes to the existing ntfy topic when:
- no Explore observation was stored in the last 48 hours;
- no successful Nairobi scan in the last 4 days;
- a due calendar strip set has been failing for two sweeps, or the scan stored 0 rows
  (if the scan's own health push does not already cover it);
- either SerpApi pool will run out before its reset at the current rate.

Push on the transition into a bad state and again every few days while it persists,
not every day. The watchdog has its own failure push and the same keepalive step as the
other workflows. Redact secrets in errors (`lib/credentials.redact_network_error`).

**A6. Weekly status push (Mondays).** One short ntfy message so that silence itself is
a signal: collector days run this week and rows stored, routes with enough history for
a baseline, Nairobi scans run, current best verified fare and its change since last
week, cheapest date combination per stay bucket (about 1, 2, 3 months). *Confirm the
wording and whether Carlos wants this push before shipping it; it is the only new
recurring notification in this plan.*

**DoD A:** tests green offline, including a fixture-driven collect-only run and
watchdog cases (fresh, stale, never-ran); one real collect-only run with its ledger
receipts shown; one forced-stale watchdog demonstration that produces the push text
without spamming the topic; `docs/notes/` entry.

## Workstream B — Nairobi tracker, reliable

**B1. Rolling window.** Departures from one month after "today" for three months, never
before 27 Dec 2026. Stays 30–90 days. `latest_return` (now 30 May 2027) must follow
from the window instead of being a fixed date. Requirements:
- The window must advance in steps (weekly is suggested), not daily, so the 14-day
  strip-freshness rule does not mark every strip as due on every scan.
- Days that enter the window are priced within one sweep cycle; days that leave are
  dropped from the radar and from the `window_best` bar (that rule already reads the
  current window from data).
- The route config, the `searches` row in Turso and the web radar must agree on the
  resolved dates, and the radar must print them.
- **No end date** (Carlos, 2026-11-14): the window keeps rolling until he changes or
  pauses the search. Do not add a cap.

**B2. Sampled stay lengths.** Add a stay step to the sweep planner (30, 37, … 86, 90).
Everything that counts coverage ("X of Y date pairs priced"), the heatmap, the grid
sampler and candidate selection must use the sampled set and say so on screen. Carlos's
standing preference: a screen states what it shows — coverage counts, units, what is
filtered out.

**B3. One-credit probe: does a calendar call return a 91-day range whole?** Through
the ledger, saved as a fixture. If yes, lift the 65-day cap for that proven length and
the sweep costs about 20 calls; if no, keep chunking at 65 (about 40 calls).

**B4. Recompute the budget** and write the table in the notes. Today: regular scans
about 91/month, sweep 62 calls every 14 days. With the new window a sweep is 20–40
calls, which leaves room to refresh more often (every 7–10 days). Keep the rule that a
sweep shrinks or waits rather than starve the regular scans. Changing the refresh
interval is a config default; say what you chose and why.

**B5. Finish the audit items that affect trust in the numbers.** Check each one's
current state first; some were fixed today.
- F5: web queries should show the latest live observation for a trip, not the minimum
  across sources' latest rows.
- F6: the OTA seller call cannot complete on round trips (`booking_token` vs
  `departure_token`). Either implement the departure-token step or stop spending the
  call. Aviasales: with 30-day stays now in range it may return rows again; measure
  before deciding to skip it.
- F1: the SearchAPI rectangle sweep is redundant and logs "unaffordable" every
  Saturday. Stop planning it. The 12 remaining lifetime credits stay untouched unless
  Carlos says otherwise.
- F7: `/ops` confidence should reflect how much of the window is priced.
- Verification lags one scan after a sweep because candidates are chosen before the
  sweep runs; choose them after it, in the same run.
- A run that crashes after storing rows but before `evaluate()` never alerts those
  rows; make the next run catch up or evaluate in a guaranteed step.
- Remove the stale "captcha-walled from CI" planner comment.

**B6. Apply the new search to production.** Changing the live `searches` row (via
`apply-route` or the script) is a production write: show Carlos the dry-run diff and
get his yes first. Then run one real scan and show the ledger receipts and the radar.

**DoD B:** tests green offline (rolling-window resolution at several dates including
the 27 Dec floor and a step boundary; sampled-stay planning; coverage counts); the
probe receipt and fixture; one real scan plus sweep on the new window with receipts;
watchdog and weekly push reading the new window correctly; `docs/notes/` entry with the
budget table.

## Order

A1–A4 → A5 → B1–B4 → B6 → B5 → A6. The collector goes first because each day it does
not run is history lost; the watchdog goes before the Nairobi changes so they are
covered from their first run.

## Rules for the executing session

- Commit to `main` (no branch, no PR). **Do not push** without Carlos's explicit yes
  each time. Nothing in A3 or B6 takes effect until pushed, so batch the ask sensibly.
- No metered call without a ledger spec and a charge-before-call event. The only new
  metered calls this plan authorises are: one real collect-only run (≤ the daily
  Explore budget), the one-credit calendar probe, and one real scan plus sweep on the
  new window.
- Product questions go to Carlos, not to assumption. Known ones: the weekly push (A6), any change to what an Explore window means (A2),
  the landing-page claim about scan frequency, and the 12 SearchAPI credits.
- Delete code the passive mode makes dead only if nothing else uses it; the full
  pipeline must remain runnable by manual dispatch for the future paid trial.
