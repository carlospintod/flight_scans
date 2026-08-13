# Is this buildable on a small budget? — 2026-08-13

Carlos's three questions, answered with measurements rather than
argument, before spending anything:

1. Do we have a definition of a vuelazo? *(No — now we do.)*
2. Can we catch error fares? *(Not with the current discovery layer.)*
3. Are we on the best API? *(No.)*

Everything below is either measured from our own database or verified
against a primary source by an adversarial research pass.

---

## 1. The cache cannot nominate deals — measured

`scripts/cache_recall.py`, run against 68 real verifications and 280
Explore routes. No API calls; all from data already collected.

### Fidelity — what the cache claimed vs what Google charged

| | |
|---|---|
| verifications with both numbers | 68 |
| **median gap (live vs cached)** | **+40%** |
| p90 gap | +204% |
| cached within 25% of live | 25/68 (37%) |
| **cached price actually usable** (live ≤ cached +10%) | **20/68 (29%)** |

Worst cases were not marginal: VLC→EVN cached 137 € was live at 464 €
(+239%); BCN→EVN cached 91 € was live at 277 €.

**71% of every verification euro is spent disproving cache fiction.**

### Coverage — does the cache see what Google sees?

| | |
|---|---|
| routes Explore (Google) found | 280 |
| also present in the cache at all | 140/280 (50%) |
| cached within 25% of Google's price | 103/280 (37%) |

The pattern in *which* routes are missing is the finding. The cache
carries the leisure/intra-EU set well — ALC→FEZ 30 € vs 30 € (+0%),
ALC→CUN 730 vs 749 (+3%), ALC→LIM 881 vs 853 (−3%). What it does not
carry at all: **ALC→JFK, ALC→BOS, ALC→IAD, ALC→EZE, ALC→GRU, ALC→GYE,
ALC→CLO.** Long-haul from a secondary origin — precisely the product.

### Freshness — the structural limit

| | |
|---|---|
| median age of a cached price when we read it | **76 h** |
| p90 | 152 h |

That matches the vendor's own documentation (2–7 days) and it is the
end of the argument. Documented error-fare bookable windows:

    < 30 min   Miami-Fortaleza (Thrifty Traveler)
    ~2 h       China Southern Chengdu, 10-30 CNY, honoured
    ~3 h       Iberia Rio-Paris, $118 vs $1,180, ~4,000 tickets sold
    ~24 h      Cathay Pacific Vietnam-North America premium
    >24 h      MAD-Santiago business ~$298, June 2022 (the outlier)

A source with a 76-hour median age cannot see a 3-hour event. That is a
fact about the source, not about our budget.

**Conclusion: cached discovery is not the cheap half of the funnel. It
is a dead end for error fares and half-blind to long-haul.**

---

## 2. What the competitors actually do

| Service | Team | Method | Mistake fares |
|---|---|---|---|
| Going | ~25 | *"sophisticated software and human Flight Experts"*; founder: *"flight experts essentially guiding the entire machine"* | **5–6/year across 187 US airports**; 16 in 2025, called *"a record-breaking year"* |
| Secret Flying | **5** | founder on record: *"a sophisticated algorithm that uses APIs to search through many flight combinations"* | ~1/week globally |
| Jack's Flight Club | 15–28 | claims a full-market sweep — no primary source found | not published |
| Fly4free | 1 → team | began as a €35 blog | — |

They are automated, as Carlos assumed. The number that reframes the
goal is Going's: **0.03 mistake fares per airport per year.** Four
Spanish origins at that rate is 0.12/year. The 2–3/year target is ~25×
Going's per-airport rate — reachable only if Spain-origin fares are
over-represented and we actually catch them, which is a cadence
question.

Useful mechanics for the detector:

- Filed-fare (ATPCO) errors **do** appear on Google Flights — our
  verification corpus is the right one.
- **OTA-side glitches never do**, so a whole category is invisible to
  a Google-only stack.
- Currency-conversion errors are tied to point-of-sale country, so many
  are invisible from a Spanish POS.
- Airlines honour ~70%; cancellation risk is front-loaded in the first
  72 hours.
- ATPCO distributes fares in batches (historically 3/day domestic,
  moving to 15/day), which sets a floor on how fast an error can
  appear or vanish downstream.

---

## 3. The API answer

Most doors are shut to a solo operator:

- **Amadeus Self-Service is dead** — hosts removed from DNS 17 Jul 2026.
  Its Flight Price Analysis endpoint was exactly the "is this a good
  deal" primitive, and it is gone.
- **Kiwi Tequila** invitation-only since 2024. **Skyscanner** partner-
  gated. **Travelport/Sabre** sales-process only.
- **Duffel** is genuinely self-serve but charges **$0.005 per excess
  search** past a search-to-book ratio — fatal for a scanner that never
  books.

So Google-derived scrapers are effectively the only option:

| vendor | per 1,000 calls | date grid? |
|---|---|---|
| **SerpAPI** (current) | **$25** entry, $9.17 at 30k | **no — explicitly deprioritized** |
| **SearchAPI** | **$4** ($40/10k) | **yes — `google_flights_calendar`, 200 date combos per call** |
| HasData | $3.08 | no |
| Bright Data | $1.50 PAYG, 5,000 records/mo free | no |

SerpAPI is ~6× SearchAPI's price and lacks the one engine this job
needs. The calendar endpoint changes the arithmetic:

    40 routes x full date grid = 40 calls, not 8,000
    refreshed every 3 h       = 320 calls/day = ~9,600/month
    fits $40/mo, with a 2,000-call/hour burst for a live event

versus today: 5 point-searches, three times a day.

---

## Decision

Discovery moves from *broad, shallow, cached* to **narrow, deep,
direct**: a watchlist of ~40 high-value routes, full date grid, every
~3 hours, on SearchAPI. The cached sweep stays only where it is
actually accurate — intra-EU leisure routes — and is no longer trusted
to nominate long-haul.

**The project is paused until Carlos purchases SearchAPI Developer**
(D1 amendment: paid flip on evidence, not calendar — this document is
the evidence). `deals.yml` scheduled runs are disabled meanwhile;
`workflow_dispatch` still works, and the Nairobi tracker (`scan.yml`)
is untouched.

### Still unmeasured, and worth doing before the flip

- **Bookability vs visibility.** Two-family agreement protects against
  cache ghosts; it says nothing about fares that die at ticketing. The
  Playwright verifier could carry N candidates to the payment page and
  measure the survival rate. Free.
- **Ground-truth base rate.** A labelled set of past Spain-origin error
  fares (route, price, hours bookable, honoured) turns "2–3/year" from
  a hope into an estimate. Collection is running; see
  `docs/notes/errorfares-groundtruth.md` when it lands.
