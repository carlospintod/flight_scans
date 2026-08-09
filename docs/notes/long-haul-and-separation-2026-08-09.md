# Long-haul funnel + project separation — 2026-08-09

Two problems Carlos raised after the first live runs:

1. *"The engine suggested BCN→TRN €37. Good deal, but the value is
   long-haul. If we don't pivot we're a Ryanair alerts system, and no
   one pays for that."*
2. *"We're using the same API keys as the Nairobi tracker, which is a
   separate project that must keep running on the free tiers."*

Both are fixed. This note records what was actually wrong (measured, not
theorised), what changed, and what is left open for Carlos to decide.

---

## Part 1 — why the engine could only find Ryanair fares

Five independent bugs, each sufficient on its own to keep long-haul out
of the alerts. Found by probing the live cache rather than reasoning
about it — one of my hypotheses ("a €300 New York fare cannot appear in
a price-sorted top-100") was flatly disproved by the data.

### 1. The sweep never looked far enough ahead

`sweep_months_ahead: 2`. Measured on the live cache: at +1/+2 months the
price-sorted top-100 from **every** Spanish origin is 100% intra-EU
(price ceiling €59–160). At +3 months the *same call* returns MAD→NYC
372, VLC→NYC 496, VLC→BKK 534, ALC→MIA 671.

Long-haul was not being out-ranked. It was not in the window.
→ `sweep_months_ahead: 6`.

### 2. One sort order, and it was the wrong one

`sorting=price` returns the cheapest N — structurally the intra-EU
firehose. `sorting=route` returns breadth across destinations. Adding
the second pass took BCN from 0 to 47 long-haul rows in one call. Both
are free.
→ `sweep_sortings: [price, route]`, limits 100 / 1000.

### 3. Unknown destinations silently became `medium`

`classify_route` defaulted unlisted IATA codes to `medium`. TRN (Turin)
is an intra-EU hop; classified as `medium`, its €37 was scored against a
Tel Aviv / Sharjah median and "saved" €275 — out-scoring a transatlantic
fare. **This is the exact mechanism behind the deal Carlos complained
about.**

→ `classify_route` now returns `unclassified` and never guesses;
unclassified and `excluded` destinations are dropped before the
cross-section is computed, and the skipped codes are logged so the list
grows from real data. ~150 codes added, all 87 unknowns from a live
probe classified, `excluded` narrowed to Russia/Belarus/Ukraine only
(the Caucasus and Central Asia are ordinary sellable destinations and
stay `medium`). A code listed in two classes is now a load-time error —
EVN and TBS were in both, silently resolved by YAML key order.

### 4. The watchlist asked for same-day round trips

`/v2/prices/latest` echoes `return_at == departure_at` on round-trip
rows. That pair went downstream unchecked, so Google Flights was asked
for a same-day return to Bangkok and answered empty — **it killed 100%
of long-haul verifications** and left the DB unable to mature a single
per-route baseline.

→ the parser drops `ret == dep`; the watchlist now uses
`prices_for_dates(depart_month=...)`, which returns real date pairs.
`latest_prices` is banned from the pipeline, with a test that fails if
anything calls it again.

### 5. Google Flights rejects the metro codes long-haul arrives as

The one that survived the first four fixes. Aviasales returns **city**
codes — NYC, CHI, WAS, LON, TYO, SEL, SAO — for exactly the
destinations the product exists to sell. Measured live, same dates:

```
MAD -> NYC   HTTP 200 "Google Flights hasn't returned any results"
MAD -> JFK   5 best_flights, typical range 370-520 EUR
```

Every metro-coded candidate died at verification for a reason that had
nothing to do with its fare. The sweep found MAD→NYC 427; verification
threw it away.

→ `routes/metro_airports.yaml` maps each metro to its gateway airport;
`verify_candidate` substitutes before querying and records
`verified_airport` in `verification_refs`. Known limitation, deliberate:
the cached metro price is the cheapest across *all* the city's airports,
so a fare that was really EWR verifies against JFK and can miss the
tolerance. That loses a deal occasionally; it never publishes a wrong
price, which is the trade that matters (non-negotiable #2).

### 6. (fallout) The widened sweep broke the observation insert

8,795 observations in one `executemany` blew Turso's 60s HTTP timeout —
the run died *after* spending its whole discovery budget. Now chunked at
400 rows.

### Measured result

| stage | before | after |
|---|---|---|
| sweep calls/run | 16 | 48 (4 origins × 6 months × 2 sortings) |
| observations/run | ~700 | 8,795 across 764 routes |
| distinct long-haul destinations | 9 | 227 |
| gate passers | — | 172 |
| long-haul verification success | 0% | live-confirmed MAD→NYC €490 (JFK, TAP) |

The queue's top candidates are now BCN→NYC 427, BCN→DEL 439, MAD→HKG
456, BCN→BKK 464, BCN→LAX 466, MAD→KUL 513 — with zero Ryanair fares in
the top 12, and the €29–37 intra-EU hops correctly killed by
`dedup_price_band` and `route_cooldown`.

---

## Part 2 — separating Vuelazo from flight_scans

`docs/MVP-SPEC.md` §1 says *"flight_scans becomes one subsystem of
Vuelazo… the quota ledger… carry the new system"* — the plan **merged**
the two. Carlos wants them separate, with the Nairobi tracker on free
tiers only. That is a change of intent from the spec, so it is recorded
here rather than assumed.

They still share a repo, a database and (today) provider keys. They no
longer share a **budget** or a **lease**:

- **Separate pools.** New source ids `serpapi_vz`, `searchapi_vz`,
  `aviasales_vz` with `backend` pointing at the same adapters. A
  distinct id gets a distinct pool for free — `quota_pools` is keyed by
  source and `spend_events` sum per source. run_deals spends only from
  the `_vz` ids.
- **Separate keys, with the borrowing made visible.** Each `_vz` source
  prefers its own env var (`SERPAPI_KEY_VZ`, …) and falls back to the
  tracker's with a loud warning. `SEARCHAPI_KEY` has **no** fallback on
  purpose: those are 100 *lifetime* credits reserved for the tracker's
  rectangle sweeps.
- **Honest anchoring.** With a shared key the provider counter reports
  the whole account, so anchoring both pools from it would let each
  believe it owns the full allowance — precisely how a deal sweep would
  eat the tracker's free 250. Shared-key `_vz` pools are therefore
  self-imposed slices (the `_ensure_service_anchor` pattern); with a
  dedicated key they anchor from that account's own `/account` probe.
- **Separate run leases.** `ledger_runs.scope` ('flight_scans' |
  'vuelazo'); the CAS predicate is scoped, so the lease is single-run
  *per project*. Before this, a nightly deal sweep could make the
  Nairobi tracker skip its scan.
- `/ops` labels every pool with its owning project.

Verified live: the run's receipt reads `aviasales_vz 48/48`,
`serpapi_vz 5/5`, invariant held, while the tracker's `serpapi` pool sat
untouched at 199 available.

---

## Open — Carlos's calls

1. **Turn on the route-specific floor?** `detector.insights_floor` is
   `false` (measured every run, enforced never). The live case for it:
   MAD→NYC €490 is a 30% "saving" against the long-haul class median and
   Google calls the same fare `price_level: typical` (range 370–520).
   The floor rejects it; the class median publishes it. Also needs a
   decision on what an *absent* Google range means — today it publishes.
2. **The daily cap is first-15, not best-15.** Today's 15 slots were
   consumed by the cron's cheap intra-EU candidates, so BCN→NYC 427 and
   BCN→BKK 464 were dropped by `daily_cap`. Changing the cap to rank
   before it cuts is a threshold *semantics* change → Carlos's call.
3. **SerpAPI capacity.** `serpapi_vz` is a holding slice of 50/mo — the
   measured headroom on the shared free key. It does not fund a daily
   pipeline. Raising it means starving the tracker or buying capacity.
4. **Dedicated keys.** Setting `SERPAPI_KEY_VZ` / `TRAVELPAYOUTS_TOKEN_VZ`
   is what separates the two projects at the *provider*; the ledger
   already separates them at the budget.
