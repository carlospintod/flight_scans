# Window-best alert — 2026-11-14

Audit recommendation #2 (`docs/notes/audits/nbo-search-2026-11-14.md`, finding F4):
push once when a verified fare beats the best verified fare the search has seen in
its current window.

## Why

`new_low` and `drop` compare each source with its own history for one trip. The
scraper's FIRST verified price for a trip has no earlier row from the same source, so
it can never alert, even at the cheapest fare the search has ever seen. The calendar
sweep now nominates hundreds of new trips, so most verified finds are first looks.
The €557 of 11 Nov (MAD 28 Jan → 20 Apr) fired nothing. The last Nairobi alert was
30 Sep.

## The rule (Carlos's answers, 2026-11-14)

| Question | Answer |
|---|---|
| What sets the bar, and what can fire it? | Verified rows only: the free scraper (`googleflights`) and SerpApi point queries (`serpapi`: the 5-pair grid, the verification fallback, the OTA seller price). Calendar cells, Aviasales quotes and SearchAPI rows never fire it and never set the bar. Any observation date counts (no lookback). Only trips inside the current window and stay range count. |
| Watch bar too? | Yes: the fare must be ≤ `watch_below_price` (€600 for this search). Routes without a bar have no ceiling. |
| Minimum improvement? | Any euro below the bar. |
| Push wording | Title `New best NBO fare: 549 EUR (was 557)`, line `[window_best] MAD->NBO 2027-01-28..2027-04-20 549 EUR (best was 557)`. Web feed badge `window best`, text `1% under the window's previous best of 557`. |

## Implementation choices

1. **New alert type `window_best`** in `lib/alerts.py` (`_window_best`). It goes in
   the existing `alerts` table, and `baseline_median` holds the previous best. No
   schema change.
2. **"This scan" is `since`** = `execute_search`'s start time, passed to
   `evaluate(since=...)`. Rows at or after it can fire. The bar is the cheapest
   verified in-window row before it. Every rule reads from the data, so there is no
   stored best and nothing to reset when the window moves.
3. **One per scan**, for the cheapest new fare. It's decided before the per-trip
   rules and replaces a `new_low`/`drop` for the same trip in that pass, so each trip
   gets one push line.
4. **The dedup has two layers.** The bar absorbs re-checks: a re-check at the same
   price can't beat itself. For idempotency, a window_best alert already recorded for
   an in-window trip at ≤ the price blocks a repeat. That covers a second
   `evaluate()` over the same scan. The mutation check showed the second layer is
   needed when two scans start in the same second, which happens in tests. Past
   window_best alerts also mute a later `new_low`/`drop` on the same trip from ANY
   source at or above their price, inside the baseline window. Without that, a
   second source's row skipped in this pass would page on the next scan.
5. **An allowlist (`WINDOW_BEST_SOURCES`), not "all but the leads"**, so a new rail
   can't start paging by accident. `searchapi` is out entirely. Its break-glass point
   queries carry the same label as its rectangle-sweep cells, and the two can't be
   told apart in `calendar_snapshots`. The break-glass rung only runs when the scraper
   isn't in the run's sources, and it has 12 lifetime credits left.
6. **Defensive:** rows with a price ≤ 0 or another currency are ignored. A zero would
   otherwise become a permanent bar.
7. **The first verified fare in an empty window doesn't fire**, because there is
   nothing to beat. This matches `new_low` needing a prior row.
8. **Every tracker search gets the rule.** It's route-generic. Only `spain-nairobi`
   is active; the one-way `p56hyykv` is paused.
9. **Push path:** `lib.alerts.summary_entry()` is now the single shape for
   `summary['alerts_fired']` in both `run_batch.py` and `run_scan.py`. It adds
   `ref_price`. `scripts/notify_ntfy.py` `price_push()` puts a window_best first and
   in the title. A summary without `ref_price` drops the "(was …)". The script still
   imports nothing from `lib`, because CI runs it as a plain script.
10. **Web feed** (`web/src/components/AlertsFeed.tsx`): it gets the `window best`
    badge and comparison text. Any euro fires, so the gap can round to 0%; under 1%
    it reads `<1% under …`. The retiring Streamlit table labels it `WINDOW BEST`
    instead of `drop`.

## Tests

`tests/test_window_best.py`, 19 tests, offline:
- a verified first look that beats the best fires exactly once, and a retry adds
  nothing;
- a re-check at the same price never fires, and one euro lower fires;
- a calendar cell, an Aviasales quote or a SearchAPI cell never fires and never sets
  the bar (the scraper verifying a lead's trip still fires against the verified best);
- the watch bar, including a fare exactly at it;
- an empty window, out-of-window and out-of-range trips;
- only the cheapest fires;
- replaces the same-trip `new_low` and mutes a second source on the next scan; a
  `new_low` on another trip still fires;
- no `since`, no rule; a zero-price row;
- **the runner path end to end**: a calendar cell nominates a trip, a fake scraper
  verifies it through `execute_search`, and the scan returns one window_best, then
  nothing;
- the push: `summary_entry`, title and line order, an old summary without
  `ref_price`, and `main()` reaching `_push`.

Suite: **444 passed**, also with sockets blocked. A mutation check confirmed the tests
catch each defect: leads counted as verified, the rule off, the alert guard removed,
the watch bar ignored. Web: `tsc` passes on the changed files, and a static render of
`AlertsFeed` shows the approved text.

## Dry demonstration (local copies of the live DB, production only read)

**A. Replay of the 11 Nov cron scan** (15,213 rows and 178 alerts up to its end,
`since` = 12:49:49Z): **one alert**, `[window_best] serpapi MAD->NBO
2027-01-28..2027-04-20 557 EUR (previous best 589)`. This is the push F4 says never
came.

**B. The next scan, simulated on today's copy** (19,183 rows, through the 14 Nov
sweep). The scraper re-checks the €557 pair at 557 and verifies three €594 cells at
602, 594 and 549 (549 made up). Result: **one alert**, `googleflights BCN->NBO
2027-01-19..2027-04-07 549 EUR (previous best 557)`. The push built from it, not
sent: `New best NBO fare: 549 EUR (was 557)` / `[window_best] BCN->NBO
2027-01-19..2027-04-07 549 EUR (best was 557)`. Then three checks:
- a retry of the same scan: 0 alerts;
- the next scan re-checks at 549, finds a 590 and a €520 calendar cell: 0 alerts;
- 1 window_best row in total.

No metered calls: nothing to show in the ledger.

## Not done / follow-ups

- **No catch-up push for the €557.** Only fares stored by a scan can fire. The next
  scan re-checks that pair; at €557 it ties the bar and stays quiet.
- **A run that crashes** after storing rows but before `evaluate()` never alerts
  those rows: they become the bar for the next scan.
- **`tracker.py alerts` and the old UI** call `evaluate()` without `since`, so they
  never run this rule. CI goes through `lib.runner`, which does.
- **`data/alerts.log` lines don't carry the alert type** (pre-existing; new_low
  doesn't either).
- **Verification still lags one scan** after a sweep (see the sweep note), so a
  sweep-found low pushes on the scan after it.
