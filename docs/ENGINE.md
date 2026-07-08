# The quota engine

How flight_scans runs a multi-source flight scanner on $0/month of API
budget — and quotes the exact per-scan cost of every search *before it
runs*, as a number the system then physically cannot exceed.

This document describes the machinery. It is the most reusable part of
the codebase: nothing in the core depends on flights.

## The problem

Free API tiers are metered by the provider, not by you. The meters are
monthly, invisible between responses, shared across every process you
run (CI cron, local CLI, web UI), and reset on the provider's schedule,
not yours. Layer several such sources with wildly different cost shapes
— a 300/month discovery API, a free-but-fragile headless browser, a
250/month managed fallback, an unmetered cache — and let multiple users
create searches against the shared pools, and the interesting question
stops being "how do I not get rate-limited" and becomes:

> Can this entire job fit inside every pool it touches, provably,
> before we spend anything — and if not, which pool was short?

Rate-limiter libraries don't answer that (they shape request *rate*
against a window they assume rolls over). API gateways don't either
(they enforce quotas *they own*; they never face a stale view of
someone else's meter). This engine exists for the asymmetry both
ignore: **the meter belongs to someone else, and your view of it is
always potentially stale.**

## The invariant

**Predicted = guaranteed upper bound.** The number shown on the search
creation form is the number the ledger reserves before the run, is the
ceiling the client guard enforces during the run, and is compared to
actual spend after the run. A scan can spend less than quoted — never
more. Searches that don't fit are skipped with a recorded reason,
never silently degraded.

Everything below is in service of making that sentence mechanical
rather than aspirational.

## Run lifecycle

```
for each active search (owner first, then stalest-first):
  plan     build_run_plan()        DB reads only — bands, candidates, pairs
  quote    cost_vector(plan)       pure function -> per-source upper bounds
  reserve  ledger.reserve()        all-or-nothing CAS; shortfall -> skip + receipt
  execute  guarded clients         charge-before-call; refuse at budget 0
  settle   ledger.settle()         attribute spend, release unused contingency
  receipt  reserved vs used        persisted per scan; asserted used <= reserved
```

## Mechanisms

### 1. Charge before the call, never refund (`lib/quota.py`)

Every metered client method is wrapped by `GuardedClient`, a proxy that
INSERTs a `spend_events` row *before* the HTTP request and marks it
`ok`/`empty`/`429`/`error` after. Failed calls stay charged, because
providers meter failed calls too. If the insert fails, the call does
not happen — fail closed. One row per HTTP attempt: internal retries go
through the guard again.

`METERED` maps method name → worst-case units. A method not listed
passes through unmetered, which is why introspection tests pin every
`*_search` / `*_prices` client method to the table (a real bug, found
2026-07-08: the one-way discovery method was missing, and one-way
scans spent HTTP calls the ledger never saw).

### 2. The ledger is primary; the provider re-anchors it

The engine never trusts its own counter as truth. Provider evidence —
response headers, free account endpoints, probes — lands in
`quota_snapshots` continuously and is promoted to `pool_anchors`.
Availability is always:

```
latest anchor baseline
  - spend recorded AFTER that anchor   (ordered by event id, not clock:
                                        all repo timestamps truncate to
                                        seconds, so same-second races
                                        are decided by the monotonic id)
  - other live runs' held reservations
  - the pool's safety margin
```

Spend recorded *before* the anchor is the provider's problem — it is
already reflected in the anchored number.

### 3. Resets are never presumed

A pool is credited only when provider evidence proves replenishment.
Two consequences, both load-bearing:

- **Monthly-429 flooring.** A 429 whose message says "monthly" while
  the anchor still shows availability means the anchor is stale; the
  pool is force-anchored to 0 (`origin='quota_429_floor'`) so the next
  run's reservations refuse it instead of re-reserving units that
  cannot be spent. Gated to monthly pools and `provider_view > 0`, so a
  late-arriving 429 cannot clobber a genuine reset.
- **The reset probe.** The rule above deadlocks: after the provider's
  reset day, headers require calls, calls require reservations, and the
  floored pool refuses reservations — forever. `needs_reset_probe`
  detects the one legal exception (pool exhausted AND anchor predates
  the expected reset) and the runner spends exactly one recorded probe
  call, promoting only snapshots timestamped after the probe started —
  a 429'd probe cannot resurrect the floor from an older positive
  snapshot. Found 2026-07-07, the day before it would have left the
  discovery pool dead past its Friday reset.

### 4. Job-level admission control via single-statement CAS

`reserve()` prices a whole search (its `CostVector`) against every pool
it touches, all-or-nothing. The guard is a single SQL statement — an
`INSERT ... SELECT ... WHERE <availability subquery> >= :units`
verified by rowcount — because the storage backend (Turso's HTTP API)
is autocommit-per-statement with no transactions. Two concurrent runs
racing for the last units produce exactly one winner; this was proven
against the real backend (`scripts/probe_ledger_cas.py`, 6/6 races).
A search that doesn't fit flips its held lines to `skipped` with a
receipt naming which pool was short, and leads the queue next run.

### 5. Contingency is reserved, not hoped for

When the free verification rail (headless browser) dies mid-batch, the
same candidates re-run through the managed fallback. That fallback
spend is quoted AND reserved up front as a `contingency` line — an
unreserved fallback either overspends the quote or silently drops
verification, both forbidden. `settle()` attributes spend primary-first
and releases unused contingency.

### 6. Tripping the guard is a bug, not flow control

In enforced mode each guarded client carries the search's reserved
budget; hitting 0 raises `QuotaExceeded`, which marks the run degraded
and is logged loudly. It is *defined* as a planner/executor divergence
— the plan said N calls and execution tried N+1 — and every occurrence
is treated as a defect to fix, never absorbed. (Shadow mode — record
everything, refuse nothing — enabled a byte-identical rollout before
enforcement flipped on.)

### 7. Quote and execution are the same object

One `RunPlan` both renders the web cost preview and drives the runner.
The creation form's instant estimate is a closed-form, DB-free function
(`predict_upper_bounds`) mirrored 1:1 in TypeScript
(`web/src/lib/predict.ts`); CI regenerates a fixture from the Python
side and re-computes it through the *actual* TS source, so the mirror
cannot silently diverge — formula drift is a red build, by design.

### 8. Source roles, not source lists

Sources are stratified by cost shape, and the join discipline keeps the
cheap ones from polluting the good ones:

| Role | Source | Cost shape |
|---|---|---|
| Discovery | Kiwi range search | 1 call sweeps a 21-day band (~50 itineraries) — scarce, 300/mo |
| Verification | Google Flights (headless browser) | free, politeness-capped per run |
| Contingency | SerpAPI | 250/mo, reserved per search, spent only on rail failure |
| Corroboration | Aviasales cache | unmetered, 2-7 days stale |

- Cached sources may *nominate* verification candidates but rank below
  any live observation regardless of recency — a stale cached fare must
  never masquerade as the current price.
- Verified prices write back into the discovery table, so every source
  converges on one itinerary-keyed price series.
- The capped verification budget is spread round-robin across departure
  months (cheapest-first within each month), so one flat fare cannot
  spend twenty calls teaching the system a single fact.

### 9. One chokepoint

Every entry point — CLI, batch runner, web-triggered scan — constructs
clients through `lib/clients.py` and wraps them there. The ledger is
not a convention; it is unbypassable.

## What shaped it (incident log)

Every mechanism above traces to a dated, observed failure:

| Date | Incident | Mechanism it produced |
|---|---|---|
| 2026-07-06 | 8 identical error stanzas for one exhausted quota | one-line monthly-429 handling, band loop break |
| 2026-07-07 | Seed anchor said 298 available; provider said 429 | monthly-429 flooring (§3) |
| 2026-07-07 | Floored pool could never see Friday's reset | reset probe (§3) |
| 2026-07-07 | Cached fare won the freshness race by construction | cached-source demotion (§8) |
| 2026-07-07 | 1-EUR price twitch fired alerts 4x | meaningful-improvement epsilon on new lows |
| 2026-07-07 | Provider sent `remaining: -1` when over quota | header clamp to 0 |
| 2026-07-08 | One-way method missing from METERED — uncharged HTTP | metered-surface introspection tests (§1) |

## What is generic

`lib/quota.py` (~700 lines: ledger, guard, reservations, settle, lease,
probes) contains no flight logic beyond two config tables and one
result-shape classifier. The batch skeleton in `run_batch.py` is ~70%
domain-free. The same triad — external-meter shadowing, job-level CAS
reservation, quoted-upper-bound enforcement — would run a GitHub API
integration or an LLM token budget unchanged. The flight-specific parts
are the planner's band geometry and the followup join discipline.
