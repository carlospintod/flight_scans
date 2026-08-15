# How often does a Spain-origin error fare actually happen?

Step 1 of the viability check. Five agents worked the public archives in
parallel (Secret Flying, Spanish "tarifa error" sites, Fly4free, aviation
press, FlyerTalk/Reddit); 266 raw rows deduped to 104 distinct fare
events, of which **99 are true mistake fares from a Spanish airport**.

Raw dataset: [`data/errorfares_spain.csv`](data/errorfares_spain.csv).

## The number — CORRECTED after verification

> **A first pass gave 2.5/year. Verification destroyed that figure.**
> Of 16 cases sent for independent second-source checking, **1 distinct
> event survived**. The collected 99 are leads, not events.

| verdict | n |
|---|---|
| other outlets covered the same fare as a **SALE**, not an error | 8 |
| **origin was not Spain** — MAD/BCN was the open-jaw *return*, real origin Prague/Dublin | 8 |
| single-sourced, no corroboration found either way | 3 |
| **confirmed** (MAD→Santiago, Jun 2022, verified twice) | 2 |

Survival rate on the checked sample: **2/21 verdicts, 1 distinct
event.** Extrapolated across the 99 collected leads that is roughly
**0.5 genuine Spain-origin error fares per year**, not 2.5 — an order of
magnitude below the "2–3 crazy error fares a year" bar Carlos set for
the project being worth the money.

Being fair to the data: "no corroboration found" is not disproof —
error fares often get exactly one write-up. But the other two failure
modes are real refutations, and both are systematic.

### Bias 1 — "ERROR FARE" is partly a marketing label

Eight cases were covered by independent outlets as ordinary sales.
Fly4free headlined the same Peru business-class fare *"CRAZY HOT!!
Business Class flights from Europe to Peru from only €487"*; Travel-Dealz
covered the July 2026 Iberia open-jaw without treating it as a mistake.
Only Secret Flying labelled them errors. **Competitor mistake-fare
counts are therefore inflated, and so was our estimate.**

### Bias 2 — open-jaw fares are not Spain-origin fares

Eight cases had Madrid or Barcelona in the *return* leg. The Secret
Flying post for one reads `DEPART: Prague, Czech Republic` /
`RETURN: Madrid/Barcelona, Spain`. The MAD→New York business €705 was
a **Dublin** departure.

This one matters operationally, not just statistically: a
Prague→USA→Madrid open jaw is invisible to a system that searches
MAD→anywhere. A meaningful slice of "Spanish" error fares are European
fares that merely *end* in Spain, and **no amount of API budget makes
our current search shape find them.**

## The origin choice is validated

**84 of 99 (85%) of all Spanish error fares departed from our four
airports.** Adding more Spanish origins buys ~15% more events for
proportionally more quota.

| origin | events |
|---|---|
| MAD | 45 |
| BCN | 34 |
| AGP | 4 |
| VLC | 4 |
| BIO | 3 |
| PMI, VGO, SVQ, LPA, ALC, … | 1–2 each |

## Where they go — this should shape the watchlist

| region | events | share |
|---|---|---|
| **Latin America** | 49 | **49%** |
| **North America** | 21 | **21%** |
| Asia | 6 | 6% |
| Europe | 1 | 1% |
| other/unclassified | 22 | 22% |

**70% of Spain-origin error fares go to the Americas.** Mexico City
recurs repeatedly (Air Europa / Aeroméxico), then Brazil, Peru, Chile,
and the US north-east. A narrow-deep watchlist should be weighted
accordingly rather than spread evenly across continents — which is a
direct correction to the current Explore rotation, where Africa and
Oceania get equal share.

**30 of 99 were premium cabin** (business/first) — including MAD→Santiago
business at €318 and MAD→Mexico City business at €366. These are the
highest-value alerts a membership can deliver.

## The trend is the uncomfortable part

| year | events (all Spain) |
|---|---|
| 2015 | 23 |
| 2016 | 28 |
| 2017 | 10 |
| 2019 | 16 |
| 2021 | 5 |
| 2022 | 5 |
| 2023 | 0 |
| 2024 | 1 |
| 2025 | 1 |
| 2026 (partial) | 8 |

An order-of-magnitude decline from the 2015–16 peak. Two explanations,
and **this data cannot separate them**:

1. Error fares genuinely became rarer — airlines improved fare-filing
   validation, and the API research independently found that "the
   bookable window has been shrinking" because airlines now detect
   errors by monitoring booking-velocity anomalies.
2. Archive-coverage bias — older posts may simply be better indexed and
   easier for an agent to enumerate than recent ones.

The 2026 partial count (8, the highest since 2019) argues against a
simple monotonic decline, and mildly against explanation 2. Worth
re-running this collection in six months against the same sources: the
delta will separate the hypotheses cleanly.

## Honest limits of this dataset

- **Only 1 of the cases got second-source verification.** Eight verifier
  agents died on a session limit mid-run. The rows are
  collection-quality, not independently confirmed — treat individual
  entries as leads, and the aggregate as an estimate.
- **0 of 99 have a recorded bookable duration.** This was the most
  valuable field and no archive states it reliably. The duration
  evidence we do have is anecdotal and comes from the separate API
  research: <30 min to ~24 h.
- Secret Flying blocks automated fetching (403/Cloudflare), so its
  archive was reached indirectly. Coverage there is good but not
  provably exhaustive.
- `normal_price_eur` is almost always 0 (unstated), so discount depth
  cannot be computed from this dataset.

## What it means for the decision

The opportunity is thinner than the first pass suggested: **~0.5
verifiable events a year**, not 2.5. At €40/month that is ~€960 per
error fare found, and only if we catch every one — which we will not,
given they last hours and a meaningful share are open-jaws our search
shape cannot see.

That does not make the business unviable, but it does relocate where the
value sits. A membership cannot be sold on 2.5 error fares a year; it is
sold on the steady stream of genuinely-below-normal fares that the price
history detector now identifies honestly, with error fares as the
occasional spike. Worth being clear-eyed about that before the purchase,
and before the landing page promises otherwise.
