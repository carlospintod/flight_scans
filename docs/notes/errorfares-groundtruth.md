# How often does a Spain-origin error fare actually happen?

Step 1 of the viability check. Five agents worked the public archives in
parallel (Secret Flying, Spanish "tarifa error" sites, Fly4free, aviation
press, FlyerTalk/Reddit); 266 raw rows deduped to 104 distinct fare
events, of which **99 are true mistake fares from a Spanish airport**.

Raw dataset: [`data/errorfares_spain.csv`](data/errorfares_spain.csv).

## The number

**2021–2026, from MAD/BCN/VLC/ALC: 15 error fares → 2.5 per year.**

Carlos's target was "at least 2–3 CRAZY error fares a year, or it isn't
worth the money". The fares exist at almost exactly that rate. The
target is not fantasy — but note carefully what this number is and is
not:

> It is the rate at which Spain-origin error fares **occur and get
> published by services that already find them**. Our own catch rate
> would be some fraction of it, set by cadence. It is a ceiling, not a
> forecast.

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

The opportunity is real but thin: **~2.5 catchable events a year across
the four origins, 70% of them to the Americas, a third in premium
cabins.** At €40/month for SearchAPI that is roughly €190 per error
fare found — if we catch all of them, which we will not.

That does not make the business unviable, but it does relocate where the
value sits. A membership cannot be sold on 2.5 error fares a year; it is
sold on the steady stream of genuinely-below-normal fares that the price
history detector now identifies honestly, with error fares as the
occasional spike. Worth being clear-eyed about that before the purchase,
and before the landing page promises otherwise.
