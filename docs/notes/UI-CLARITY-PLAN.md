# UI clarity plan — tracker radar + /ops

Status: implemented 2026-11-13. See "Implementation notes" at the end for
what changed from this plan and why.

## Problem

Carlos: "it looks nice but I don't understand very well what's happening."
The visual language is fine and stays. The problem is that the pages show data
without saying what it means, how much of it there is, or what to do next.

Observed on https://flight-scans.vercel.app on 2026-11-13 (window 27 Dec – 30 May,
60–90 day stays, last scan 2 days ago):

1. **The price grid looks broken.** MAD→NBO is one giant pale rectangle (1 cell),
   BCN→NBO is three. Causes in `web/src/components/Heatmap.tsx`: the axes are built
   only from dates/stays that have data, so 4 observations stretch to fill the whole
   chart; and when every cell has the same price `visualMap` gets `min === max` and
   renders a washed-out colour. Nothing says "only 4 of ~N date pairs scanned so far".
2. **Alerts contradict the hero.** Hero says cheapest is 557 EUR; the alerts list shows
   "NEW LOW 481 EUR" for Nov–Jan trips that are outside the current window (and partly
   in the past). `getRecentAlerts` in `web/src/lib/queries.ts` filters by route only,
   not by the current window. "was 571" is unexplained (it is the baseline median).
3. **No statement of coverage or freshness per section.** The landing terminal replay
   says "3,561 prices" while the live page beneath it holds a handful of rows. A row
   seen 20 days ago sits next to rows seen 2 days ago with only a dimmed colour.
4. **Section titles name the chart, not the question.** "Price grid · departure × stay
   length", "Who has the cheap fares" (bars labelled 5, 3, 2, 1 with no unit).
5. **/ops is ten stacked sections of equal weight**, mixing two projects (Nairobi
   tracker and Vuelazo) under the heading `OPS · spain-nairobi`. There is no top-level
   answer to "is everything OK and is there something I need to do?".

## Constraints

- Read `web/AGENTS.md` first: this Next.js has breaking changes; consult
  `node_modules/next/dist/docs/` before writing code.
- No new queries per request on the public page (ISR, Turso read budget). Extra numbers
  must come from the queries `SearchRadar` already runs or from one added aggregate
  inside the same `Promise.all`.
- No change to tokens, fonts, colours or the landing hero animation. This is a clarity
  pass, not a restyle.
- `(vuelazo)` pages are out of scope. Spanish member-facing copy is not touched.
- Tracker/ops copy is English, as today. New explanatory sentences are listed under
  "Copy for Carlos to approve" — do not invent others; copy is a product decision
  (root `CLAUDE.md`, Prime directive).
- Any cost preview shown in the web app must keep matching the Python planner
  (estimator-drift test). Do not touch `capacity*.ts` numbers.

## Phase 1 — public radar (`SearchRadar` and its children)

Files: `web/src/components/{SearchRadar,PriceHero,AlternativesTable,Heatmap,AlertsFeed,CarrierBar,Section,StaleBadge}.tsx`, `web/src/lib/queries.ts`, `web/src/lib/types.ts`.

1. **Summary strip under the route heading** (new small component). One line each:
   what is being searched, how much of it has been scanned, when. Example shape:
   "Searching MAD/BCN → NBO, leaving 27 Dec – 1 Mar, staying 60–90 days ·
   4 of 1,240 date pairs priced in the last 30 days · last scan 2 days ago, next Mon".
   Coverage = distinct (origin, departure, return) with a fresh snapshot ÷ pairs the
   window allows. Compute the denominator in TS from the window; the numerator from the
   heatmap grids already fetched (sum of cells), so no extra query for round trips.
2. **Every section gets a one-sentence subtitle** via an optional `hint` prop on
   `SectionHeading`. Titles become questions/answers (see copy list).
3. **Heatmap honest about sparsity.**
   - Build the x axis from every departure date in the window and the y axis from
     `minStay..maxStay`, so unscanned pairs are visibly empty cells.
   - Fixed cell size (keep the horizontal scroll container); never stretch.
   - When `min === max`, use a single colour and hide the visualMap gradient.
   - Under ~15 cells, show the empty-state line with the coverage count above the grid
     instead of pretending the grid is informative.
   - Legend text: "cheaper → pricier (EUR)" with the min and max values.
4. **Alerts scoped to the current search.** Add window bounds to `getRecentAlerts`
   (departure ≥ earliestDeparture, return ≤ latestReturn, stay within min/max). Pass
   `w` instead of `routeId`. Row text shows the drop: "481 EUR · 16% below the usual
   571". Empty state when none in window. (Open decision 1 covers the old ones.)
5. **Alternatives table.** Rows older than the freshness cutoff get an explicit
   "may be gone" tag instead of only dimming. Keep columns.
6. **Carrier chart.** Axis/tooltip unit: "times cheapest". Subtitle states the sample
   size ("of N priced trips").
7. **Hero ↔ data consistency.** `LandingHero` terminal replay is a recorded scan; label
   it with its date so its numbers are not read as live ("replay of the 11 Sep scan" —
   use the real date of the recorded run, or drop the figures if unknown).

## Phase 2 — /ops (`web/src/app/(tracker)/ops/page.tsx`, `components/ops/*`)

1. **Status header first**: three or four tiles answering "do I need to act?" —
   deals waiting for approval, last scan status and age, sources in alarm
   (payment_walled / dark / auth_failed), lowest remaining budget. All from data the
   page already loads.
2. **Group by project, then by job**, replacing the flat stack:
   - Vuelazo: Deal queue · budgets and health for `_vz` pools, anthropic, telegram, resend.
   - Nairobi tracker: Run a scan · Search settings · scan history · tracker pools.
   - Admin: Users & invites · API keys.
   Use anchored sections with a sticky in-page nav; no new routes.
3. **Merge the three per-source views** (Spend, Source health, API budgets) into one
   table per project: source, status, remaining/limit, calls 30d, failed, last ok.
4. Page title becomes "OPS"; the route id moves into the tracker group.
5. Each group gets the same one-line `hint` treatment as Phase 1.
6. Login page: add one line saying where the password comes from
   ("APP_PASSWORD in the Vercel project env") and surface the 429 lockout message
   distinctly from "wrong password".

## Verification

- `npm run lint` and `npm run build` in `web/`; existing web tests green.
- `.\.venv\Scripts\python.exe -m pytest -q` still green (nothing Python should change).
- Browser check against local dev with the Turso env from `web/.env.local`: radar at
  the current sparse window (grid shows empty cells, coverage count matches a manual
  SQL count), one `/s/[slug]` one-way search (curve branch unaffected), /ops logged in.
- Screenshots of radar and /ops before/after in the PR description.

## Open decisions for Carlos (do not assume)

1. Old alerts outside the current window: hide them (recommended), or keep them in a
   collapsed "earlier windows" list?
2. Phase 2 grouping puts Vuelazo above the Nairobi tracker on /ops. OK, or tracker first?
3. Is anything else unclear that is not listed under "Problem"? The list is what an
   outside reader tripped on; Carlos's own confusion may be elsewhere.

## Copy for Carlos to approve

| Where | Proposed text |
|---|---|
| Cheapest observed | "The lowest price seen for any date pair in this search." |
| Cheapest per departure day | "Best trips found — one row per departure day, cheapest first." |
| Price grid | "When to leave × how long to stay. Each square is one trip; empty squares have not been priced yet." |
| Price alerts | "Times a price dropped well below what that trip usually costs." |
| Carriers | "Which airline was cheapest, counted across priced trips." |
| Stale row tag | "may be gone" |
| Coverage line | "{n} of {total} date pairs priced in the last {d} days" |

## Implementation notes (2026-11-13)

Decisions taken:

- **Open decision 1 (Carlos):** hide alerts outside the current window. Done in SQL
  (`lib/db.recent_alerts` gained optional window kwargs, tested in
  `tests/test_recent_alerts_window.py`; `getRecentAlerts(w)` mirrors it) so `LIMIT`
  applies after the clamp. Today the window has zero alerts, so the section shows its
  empty state.
- **Open decision 2:** took the default (Vuelazo above the tracker on /ops). Reordering is
  moving one `<Group>` block in `web/src/app/(tracker)/ops/page.tsx`.

What the live data showed (it changed the plan):

- The grid looked empty mostly because `getHeatmapGrid` dropped every fare over €600:
  20 date pairs were priced in the window (last 21 days), only 4 at or under €600. The
  Python original (`ui/_common.latest_grid_for_heatmap`) has no ceiling. The grid query
  now drops it too, and `Heatmap` clamps the colour scale at `MAX_DISPLAY_PRICE`
  instead: green→red for ≤ €600, one grey for pricier, dark for not priced. The hero,
  table, curve and carrier views keep the ceiling.
- The heading showed 27 Dec – 30 May; the departure window is 27 Dec – 1 Mar
  (`latest_departure`). `RouteWindow` now carries `latestDeparture`; `lib/window.ts`
  holds the departure-bound geometry (same bound as `predictUpperBounds`).
- `new_low` alerts store the trip's previous all-time low in `baseline_median`, not a
  median. The approved hint ("well below what that trip usually costs") was wrong for
  them, so the hint and each row now say which one it is ("under its previous low of
  571" vs "under its usual 571"). **Copy deviation, needs Carlos's OK.**

Implementation choices:

- Coverage numerator = cells of the grids already fetched (no extra query); departures
  past the cap are filtered in `SearchRadar`. Denominator = `datePairsPerOrigin(w)` ×
  origins. One-way searches show no coverage line (the curve query keeps the ceiling, so
  its count would undercount).
- ECharts throws "Heatmap must use with visualMap" for any cartesian heatmap series
  without one, so the solid-colour series get hidden one-colour visualMaps.
- /ops: the three per-source panels (spend, health, budgets) merged into one table per
  project (`components/ops/OpsStatus.tsx`). The "lowest budget" tile ignores snapshots
  older than 30 days; otherwise retired pools (Sky Scrapper 0/20, 157 days old) would
  keep it red forever.
- New copy not in the approved table (internal or factual): /ops group hints, the
  hero's "No fare at or under €600 yet…" state, the login hint, "may be gone".
- The old /ops page used classes that don't exist in the theme (`text-fg-dim`,
  `border-line`, `bg-bg-2`, `text-matrix`, `text-cyan`); replaced with real tokens.

Not done:

- "next scan" in the summary line. The cron schedule and `cadence_days` skip logic
  make a predicted date easy to get wrong.
- Clicking a grid cell to open the trip page.

Found, not touched:

- The "Sources in alarm" tile shows Kiwi as payment-walled: it has had 0 calls in 30
  days and was retired on 2026-07-13, but `lib/health` still reports it.
- All 6 queued deals were created 8–12 Aug 2026 and 5 have travel dates already past.
  Every Vuelazo source has been idle since mid-August, so nothing expires them.
- `npx eslint src` reports 4 errors and 3 warnings, all in files this pass didn't touch
  (`join/page.tsx`, `searches/page.tsx`, `api/member/login`, `CuentaPanel`,
  `CredentialsAdmin`, `UserAdmin`).

Verified: `pytest -q` (396 passed), `tsc --noEmit`, eslint on changed files,
`node scripts/check-estimator.mjs` (0 drift), `next build`, and a local dev run against
the live DB. Checks covered radar at desktop and phone width, grid tooltips matching the
hero fare, coverage counts matching a manual SQL count, and /ops at desktop and phone
width.
