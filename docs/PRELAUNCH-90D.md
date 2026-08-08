# Vuelazo — Pre-Launch Plan (~13 weeks)

Repo destination: `docs/PRELAUNCH-90D.md`
Sequencing per Carlos: **product first, audience second** — no public activity until the
machine works. The one gift of this ordering: fare-history seeding runs silently from M0,
so the baseline cold start and the build overlap for free. Weeks are elastic; gates are
not — each phase ends at its gate, not at its date.

## Phase 1 — Build (weeks 1–6). Public footprint: zero.

**W1 — Skeleton + accounts.**
Ship M0. Register vuelazo.es (+ check if vuelazo.com is parked/buyable; 10-min OEPM/EUIPO
name search). Create accounts: Travelpayouts, SerpAPI, Resend, RapidAPI (Skyscanner
proxy), Anthropic API key for drafting. DNS + SPF/DKIM/DMARC on vuelazo.es. Telegram bot
+ private test channel. **Seeding clock starts the day M0 first runs.**

**W2–3 — Detector.** Ship M1. Produce the three week-1 audits (cache freshness `market=es`,
`price_insights` coverage, everywhere-probe verdict) — these arbitrate D1's reversal
clause. Begin the daily 5-minute shadow habit: glance at what the gate would have queued.

**W4–5 — Membership.** Ship M2 (all in Stripe test mode; no autónomo needed yet).

**W6 — Publishing.** Ship M3, including the deliverability seed test (Brevo fallback
decision point per D4).

*Phase gate:* 7 green cron days, M0–M3 DoDs met, audits filed.

## Phase 2 — Shadow ops + brand freeze (weeks 7–9). Public footprint: still zero.

**W7–8 — Shadow mode.** Run the real 10-minute daily loop into the private channel as if
members existed. Tune thresholds from rejection taps. Track the D0b metric: excellent
deals/week from VLC/ALC.

**W8 — Beachhead checkpoint (D0b).** VLC/ALC sustaining ≥3 excellent deals/week →
beachhead confirmed. If not → flip channels/copy to MAD/BCN now, before anything public
exists. Cheap day either way; decide and log.

**W9 — Site + brand freeze.** Ship M4a (and M4b if on schedule — else cut, per D6).
Freeze logo, tokens, card template. Draft the founder-story post and the press pitch
(es + val versions of the local angle).

*Phase gate:* production site live behind "próximamente" or soft-public archive;
2 consecutive shadow weeks with ≥5 approvable deals each; beachhead decided.

## Phase 3 — Audience (weeks 10–13). Free only; no payments yet.

**W10 — Doors open, quietly.** Public Telegram channel + Instagram live; site archive
public; free email signup on. Founder-story post published on the site. Seed friends &
family; first deal cards.

**W11 — Communities.** Join and genuinely participate in VLC/ALC travel + chollos
Facebook groups; first best-deal shares (≤2–3/week). ForoCoches/Menéame/r/spain moments
when a deal is truly exceptional — real numbers, no astroturf.

**W12 — Press push.** Pitch Valencia Plaza, Levante-EMV, Las Provincias, Información
(Alicante) + one consumer-tech outlet with the founder story and a screenshot-able
provable-savings example. Open the **founding-member waitlist** ("€29/año para los
primeros — sin renovación automática") on the site.

**W13 — Paid launch gate.** Trigger conditions, all three: (a) free base ≥ ~400
subscribers across channels *or* one press hit landed (scarcity needs an audience);
(b) 4 consecutive weeks of 5–7 quality deals; (c) autónomo registration + Stripe live +
gestor sign-off on legal pages and tax posture (the registration was deliberately
deferred to this moment). Then: flip payments, email the waitlist, founding price live,
14-day guarantee prominent.
If (a) falls short, extend Phase 3 rather than launching paid into an empty room — the
founding offer only works once.

## Standing rhythm from W10 (the 2-hour week)

~10 min/day queue · 15–20 min Sunday digest · ~20 min/week cards + community posts ·
5 min metrics glance. Alarms that interrupt the rhythm: ledger near ceiling,
deliverability complaints, cron red >24h, refund spike.
