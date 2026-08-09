# Vuelazo — Decision Log

Repo destination: `docs/DECISIONS.md`
Status: **closed**. Every product decision below is final for v1. Claude Code makes
implementation choices only. If an ambiguity in this log blocks implementation,
stop and ask Carlos — never resolve product questions by assumption.

Legend: [R] = ratified explicitly by Carlos in the planning session.
[D] = delegated to the session by Carlos ("make the rest of decisions as you deem best").

---

## D0 — Origins instrumented [R]

**Choice:** MAD, BCN, VLC, ALC polled from day one.
**Rationale:** Under the three-layer sourcing design, discovery is nearly free and paid
verification is candidate-capped, so four origins cost barely more than two. Data
optionality now, strategy later.
**Rejected:** two-origin start (the stated cost reason didn't survive the cost model).
**Reversal cost:** trivial (config).

## D0b — Audience beachhead [D]

**Choice:** Launch channels are **VLC + ALC** ("vuelazos desde tu aeropuerto").
Exceptional MAD/BCN fares are included and honestly tagged ("desde Madrid — 1h40 en
AVE desde València"). Dedicated MAD/BCN channels are the season-2 expansion, after the
machine is proven.
**Rationale:** The free incumbents (Viajeros Piratas, Exprime Viajes) default to MAD/BCN;
a paid product from zero followers fights them head-on where their coverage is densest.
VLC/ALC is the uncontested gap, Carlos has local credibility in the catchment, and niche
local communities are more penetrable than metropolitan noise. The national ambition is
served by instrumenting all four origins now (D0).
**Rejected:** MAD/BCN-first launch (kept as the explicit fallback).
**Reversal trigger (scheduled):** Week-8 checkpoint of the pre-launch plan. If seeded
data shows VLC/ALC cannot sustain ≥3 genuinely excellent deals/week, flip the beachhead
to MAD/BCN **before** audience-building starts. Reversal cost at that point: near zero
(channel naming and copy only).

## D1 — Fare data sourcing & polling economics [R]

**Choice:** Three-layer design.
- **Layer 0 — free discovery:** Travelpayouts/Aviasales cached Data API (token at
  signup; per-minute rate limits; cache 2–7 days). Origin-only "anywhere" sweeps 3–4×/day
  per origin + daily watchlist refresh (~35 destinations × 4 origins). Skyscanner-proxy
  "everywhere" search probed as a supplement (RapidAPI free tier).
- **Layer 1 — paid live verification:** every alert requires a live Google-family
  confirmation. Build phase: SerpAPI free 250/mo + existing Playwright scraper as
  corroboration (SearchAPI free credits are one-time; 2 remain — break-glass only).
  Launch phase: **SearchAPI.io Developer, $40/mo, 10,000 searches** becomes the workhorse
  (verifications + calendar rectangles ≈ 2,200–4,000 calls/mo of the 10,000).
- **Layer 2 — judgment:** baselines + scoring (D2).
**Phasing [R]:** €0 stack for as long as free tiers genuinely hold; paid components enter
on evidence (free-tier ceilings measurably limiting quality, or launch reliability needs),
arbitrated by the quota ledger. The ledger is unbypassable; predicted spend = hard bound.
**Rejected:** Amadeus Self-Service (closed to new registrations, Mar 2026); Kiwi Tequila
(invite-only; Travelpayouts' Kiwi program requires 50k MAU); SerpAPI paid (~6× SearchAPI's
per-call rate at our volumes; free 250/mo kept as contingency rail); scraping as
load-bearing verification for a paid product (fragility; demoted to corroboration).
**Reversal trigger:** week-1 audits — Aviasales cache freshness/coverage for Spanish
origins (`found_at` analysis, `market=es`) and `price_insights` coverage. If the free
cache is too thin, promote paid discovery: SearchAPI calendar rectangles across the
watchlist still fit the ceiling (196-combo geometry, ~1,700 calls/mo biweekly).

**Monthly cost model (launch scale):** SearchAPI ~€37 + Anthropic API drafting ~€4 +
Resend Pro ~€19 (from ~100 subscribers) ≈ **€60/mo**, vs €50 soft ceiling — flex
explicitly blessed by Carlos. Pre-revenue phases run €0–20.

**Amendment (Aug 2026):** Google Travel Explore engines now exist on both SerpAPI and
SearchAPI, adding live origin→anywhere discovery to the Google family — unavailable
when this decision was written. Single-paid-API pick reconfirmed as SearchAPI Developer
($40/10k): includes Explore and the calendar engine (SerpAPI's date grid remains
unbuilt, roadmap-frozen), at 1/6 the per-call price. SerpAPI paid remains rejected; its
free 250/mo is re-pointed Explore-first. Paid flip trigger unchanged (evidence, not
calendar); bounded 1–2 month seeding sprint authorized as the low-burn pattern.

*Implementation notes (2026-08-09, measured — see
`docs/notes/long-haul-and-separation-2026-08-09.md`):*

- Vuelazo runs its **own** SerpAPI account (`SERPAPI_KEY_VZ`, 250/250 probed). The
  Nairobi tracker keeps its own; neither can spend the other's (`lib/sources.py`
  `_vz` source ids, `ledger_runs.scope`).
- Explore is wired provider-agnostically (`lib/explore_api.py`, engine
  `google_travel_explore` on both vendors). **The SearchAPI flip is one config line**
  — `explore.provider` in `routes/vuelazo.yaml` — with no parsing changes.
- **Undirected Explore calls are a trap.** `departure_id` alone returns Google's
  default list: Europe-heavy, 66 destinations, nothing above €325 — the same intra-EU
  bias that made the cached sweep useless for long-haul. Long-haul requires
  `arrival_area_id` (continent kgmid). Europe is deliberately excluded from the area
  list: the free Aviasales sweep already covers it at zero quota.
- With areas, one MAD/North America/November call returned EWR 398, YYZ 429, ORD 448,
  IAD 467, LAX 478, SFO 487 — real airport codes (not the metro codes Google Flights
  rejects) and real date pairs, from the same corpus verification queries.
- Efficiency: the grid (origins × areas × months = 120 windows) is walked by a
  deterministic day-keyed rotation at `calls_per_day` (4 ≈ 120/mo), leaving ~130 of
  the free 250 for verification. Verification itself is now free-first (Playwright
  scraper), so SerpAPI only pays for publish-bound candidates.

## D2 — Deal detection [R]

**Choice:** Hybrid detector, **implemented incrementally** ("start simple, tune as we
go" — ratified interpretation: day-one rules are absolute route-class floors + same-day
cross-sectional comparison; per-route percentile gates switch on automatically per route
as history accumulates, mirroring the existing `min_observations` pattern).
- **Baseline (mature state):** per-route trailing 60-day distribution of verified fares;
  gate = ≤ P10 **and** ≥25% below trailing median **and** absolute savings ≥ route-class
  floor (≈€30 intra-Europe / €80 medium-haul / €150 long-haul). Percentiles, not z-scores
  (fare distributions are skewed/multimodal). Seasonality buckets in v1.5.
- **Cross-route score:** depth-below-median + absolute € saved + route-class aspiration
  weight (long-haul rarity earns its multiplier). Deliberately crude; Carlos's approve
  tap is the last mile.
- **Cold start:** watchlist polled from M0 onward; target ≥8 weeks of history before any
  paid member sees an alert. Interim heuristics: absolute floors, same-day cross-section,
  and Google `price_insights` typical-range where it populates (coverage audit in week 1).
- **Mistake fares:** crude catch (below 50% of trailing P25, or under hard floor); fast
  lane to live verification; **two independent coverage families required** before a
  mistake-class alert (OTA-teaser and cache-ghost protection); published with the honest
  airline-may-not-honor caveat. Realism: error fares are fireworks, not dinner.
- **Non-negotiable guardrails from day one [R]:** live verification before any alert;
  daily candidate cap ~15; per-route cooldown (7 days unless a further −10%); dedup by
  route+price band; cached-only observations never reach the queue; every rejection
  records a one-tap reason (the tuning signal).
**Rejected:** pure statistics (can't rank across routes, mute on cold routes); ML
forecasting (data, tuning time, and solo-maintainability all say no — revisit never,
until the company earns a data scientist).
**Reversal triggers:** <3 excellent deals/week across 4 origins after 8 weeks → loosen
gates/floors; >25 candidates/day after controls → tighten.

## D3 — Curation workflow [R]

**Choice:** Machine drafts, Carlos decides. Pipeline discovers→gates→verifies→scores→
drafts the full Spanish write-up (Anthropic API, versioned prompt template in repo).
Ops console shows ≤15 cards/day (draft, sparkline, confidence, verification link). Three
actions: approve (publish event) / reject with one-tap reason / edit-then-approve.
Discipline: editing >1 in 5 drafts means fix the template, not the schedule. Mistake-class
candidates bypass the daily ritual via ntfy push; 30-second phone approval. Weekly digest
self-assembles from the week's approvals; one Sunday review.
**Time arithmetic:** ~10 min/day + 15–20 min digest ≈ 90 min/week, inside the 2h budget;
the remainder is why social content must be generated, not written (D8).
**Rejected:** fully manual (dies on the time budget); full auto-publish (sells out the
human-QA differentiator; one bad auto-published deal costs more than a hundred good ones
earn). Auto-publish to the *free delayed* channel is discussable in v1.5 with a track
record.

## D4 — Delivery stack [R]

- **ESP: Resend** (Carlos's call, ratified with eyes open). Free tier (3,000/mo, 100/day,
  no vendor branding, React Email) carries build + first ~100 subscribers; **Pro $20/mo
  (50k emails)** from the point digest day exceeds 100 recipients. Digest and alerts go
  through the **plain email API** (batch), never Resend's contacts-priced Marketing track
  ($40/5k contacts — avoided by design; the list lives in Turso because membership
  requires it anyway). Obligations logged: one-click List-Unsubscribe headers + own
  suppression list (Gmail/Yahoo bulk rules); SPF/DKIM/DMARC on vuelazo.es before first
  send; gradual warm-up; pre-launch seed-list inbox-placement test.
  **Named fallback:** Brevo (EU processor, 300/day free, €9 Starter) if the seed test
  shows placement problems.
- **Telegram gating:** native Bot API, no third-party gatekeepers. Stripe webhook →
  member row → email deep link `t.me/<bot>?start=<one-time-token>` → bot binds Telegram
  identity to member row → single-use invite / join-request approval into the private
  channel. Lapse/refund webhook → bot removes. Public free channel ungated.
- **Free/paid line [R, research-calibrated]:** free channel + weekly digest carry 1–3
  *genuinely excellent* deals/week, published 24h after members; paid gets all 5–7,
  instantly, with per-airport filtering as the personalization layer. Receipts mechanic
  used sparingly and truthfully ("este vuelazo voló en seis horas — los miembros llegaron
  a tiempo"). Free quality is the trust engine — never junk, never a crippled demo.
- **Latency SLOs:** approve-tap → member phones <5 min; detection → queue <4h standard,
  <1h mistake-class (when awake). Public promise is "alertas al instante" relative to
  publication; detection latency is never promised publicly.

## D5 — Membership & payments [R]

- **Platform:** extend the existing Next.js/Vercel app (auth, sessions, ops console,
  design system already exist; D4 gating already assumes our own webhook + member rows).
  Rejected: Ghost (integration seam, fee, theming ceiling), hosted membership platforms
  (design control ≈ 0 kills the differentiation; fees; no programmatic pages).
- **Payment shape [R]:** **one-time 12-month pass** for everyone. All methods including
  **Bizum** (no recurring support in Stripe → one-time unlocks it). No auto-renewal
  anywhere in v1 — "sin renovación automática — tú decides cada año" is checkout copy
  and a trust asset. Renewal = T-30/T-7 reminder emails; founding price survives only on
  on-time renewal (the retention mechanic). Acquisition over retention, per Carlos.
  Reversal: opt-in auto-renew toggle for card users is a v2 addition if year-one renewal
  is poor.
- **Pricing [R]:** list **€39/año IVA incluido**; founding cohort **€29/año**, locked
  while membership stays active via on-time renewal. Annual only. **14-day money-back
  guarantee** [R] — which *is* the statutory withdrawal window, honored rather than
  waived: zero legal gymnastics, marketed as the guarantee. Review trigger: refund
  patterns or "no vi valor" churn reopen the window length. Net math: €39 → ~€32 net,
  €29 → ~€24 net; ~25 founding members ≈ launch-phase run-cost break-even.
- **Legal/tax posture (design assumptions — gestor sign-off required at autónomo
  registration; nothing here is legal or tax advice):** Stripe Tax on from first sale;
  B2C prices displayed IVA-included; 21% Spanish VAT while sales are domestic; OSS only
  if cross-border EU B2C digital sales exceed €10k/yr (Stripe Tax calculates/collects,
  filing stays with the gestor). Sequencing [R]: free audience first; autónomo
  registration only when payments flip on.

## D6 — Programmatic SEO [R]

**Choice:** **v1, sacrificial.** Route pages (`/vuelos-baratos/valencia/roma` pattern)
generated from our own data: price-history chart (components exist: `HistoryChart`,
`PriceCurve`, `Heatmap`), provable "precio normal", recent best fares, data-derived
best-booking-window note, free-alerts CTA. Quality gate: a page publishes only when its
route passes the detector's own `min_observations` bar; thin routes stay `noindex` until
data matures. Launch cohort: 4 airport hub pages + ~40–60 gated route pages, each with a
once-generated, quarterly-refreshed Claude intro (no two pages read templated).
**Discipline:** it ships **last** before launch and is the first and only thing cut to
v1.5 if the timeline slips — its delay costs months-later traffic, not launch viability.
Honesty: an investment tranche, not a launch lever; day-one growth comes from D8.

## D7 — Brand: visual direction & editorial voice [D]

(Name **Vuelazo** [R]; vuelazo.es registration = week-1 task; check whether vuelazo.com
is parked/purchasable. Adjacency logged: 99viajes.com exists (Barcelona travel page);
weak collision, accepted. Trademark: 10-minute OEPM/EUIPO search before spending on
brand assets — Carlos's job at registration.)

**Visual direction:** *editorial data-design*, the deliberate opposite of clipart-pirate.
Principles, in force for every surface (site, emails, deal cards, charts):
- **The chart is the brand.** Every deal ships with its price-history sparkline; the
  provable "precio normal → hoy" frame is the visual signature no incumbent can copy.
- Type-driven layout, generous whitespace, warm paper-tone background, near-black ink,
  **one** signal color reserved exclusively for prices and CTAs (warm amber/naranja
  family — implementation picks the exact token). No stock beach photos, no mascots, no
  urgency-red, no ALL-CAPS hype.
- Typography: one characterful display face + one workhorse text face, open-source
  (candidate pairing to evaluate in implementation: Fraunces or Instrument Serif for
  display; Inter or Instrument Sans for text). Evolve the existing `design-system.html`
  and web tokens — do not start from zero.
- Emails are React Email components sharing the site's tokens: the inbox and the site
  are visibly the same object.
- Tagline: **"Vuelazos desde tu aeropuerto."** Hook, stated once and repeated forever:
  **"El precio normal, demostrado — y el chollo, a tiempo."**

**Editorial voice (encoded in the drafting template — see MVP-SPEC §Drafting):**
es-ES, second-person tú, precise and warm, short sentences. Every alert contains: route;
date windows; today's price vs normal price with % (from our baselines — never invented);
carrier with bag/fare-class reality for ULCCs; how to book (deep link, direct — see D9);
caveats (error-fare honor risk where applicable). Savings framed honestly; superlatives
earned by numbers, never by punctuation. Forbidden: "¡INCREÍBLE!", clickbait ellipses,
fake scarcity. Regional flavor: warm neutral Spanish; Valencian touches allowed in social
copy, never required for comprehension.

## D8 — Growth loops [D]

**Choice:**
- **Instagram: yes.** Deal cards (1080×1350) auto-rendered from deal data by the pipeline
  (same tokens as the site; renderer in M3). Posting is manual-from-phone in v1 (~30s/day;
  API auto-posting deferred — approval overhead not worth it yet). 3–5 cards/week, only
  approved deals.
- **TikTok: no** for v1 (video production violates the 2h/week budget). Revisit ≥500
  members.
- **Local Facebook groups: yes, as a genuine member.** VLC/ALC travel and chollos groups;
  ≤2–3 posts/week, best deal only, never spam cadence. Group list built in audience phase.
- **Press & forums (the JFC/Going playbook, translated):** founder-story pitch — "analista
  de datos de Castellón construye un detector de chollos de vuelo para València y
  Alacant" — to Valencia Plaza, Levante-EMV, Las Provincias, Información (Alicante), plus
  consumer-tech angles; Menéame and ForoCoches (viajes) moments; Reddit r/spain with real
  numbers, AMA-style, respecting self-promo norms. One great press hit outperforms months
  of posting (JFC's front-page AMA: +42k subscribers).
- **Referral program: no** at launch. Adds code and fraud surface; forwardable deal cards
  *are* the referral loop. Revisit ≥500 members.
- **Paid ads: no.** Neither case-study company needed them; neither does v1.
**Reversal:** channel mix reviewed monthly against subscriber growth; anything not
compounding gets cut without ceremony.

## D9 — Fallback economics / affiliates [D]

**Choice:** **Clean at launch — both tiers.** Deal links go direct (Google Flights /
airline), zero affiliate wrappers. "No ganamos nada con tus clics — solo con tu
membresía" is a stated differentiator against affiliate-funded incumbents whose editorial
incentives it quietly indicts, and it protects the "precio normal, demostrado" trust
position.
**Rationale:** flight affiliate payouts at our early volume are beer money; the trust
position is the business. The Travelpayouts account (needed for data anyway) keeps the
option warm at zero cost.
**Reversal trigger:** if run costs exceed member revenue at month +6 post-launch,
affiliate links on the *free tier only* reopen for decision.

## D10 — Scope guards: NOT in v1 [D]

Going's stated early-growth key was refusing everything adjacent. Ours, each with the
one-line reason:
1. **MAD/BCN audience channels** — data yes, channels no (D0b); expansion is season 2.
2. **Hotels, packages, car hire** — the focus *is* the moat.
3. **Points & miles content** — different expertise, different audience, infinite rabbit
   hole.
4. **Mobile app** — Telegram push *is* the app; an app is a company-sized commitment.
5. **Auto-renewing subscriptions** — deleted by D5; v2 opt-in at most.
6. **Monthly billing** — churn machine, support tax, breaks the one-booking-pays math.
7. **Referral program & paid ads** — D8.
8. **TikTok / video** — D8.
9. **Auto-publish** — approval-only stands (D3); free-channel auto-publish is a v1.5
   discussion with a track record.
10. **Affiliate monetization** — D9.
11. **Other languages / markets** — es-ES only; Valencian/English are later luxuries.
12. **Community features** (comments, forums, chat) — moderation is a time-budget bomb.
13. **Public API / B2B** — not before the consumer product earns its keep.
14. **ML forecasting** — D2's rejected option stays rejected.

---

## Growth guidelines [R] (from the JFC / Going research — standing editorial law)

1. The founder's own need is the brand: the Nairobi tracker story leads the About page,
   launch post, and every press pitch.
2. The free tier is the marketing department and must be genuinely excellent (~87% of
   Going's list stayed free forever — by design).
3. The paid line is completeness + speed + personalization ("solo tus aeropuertos") —
   never quality.
4. The hook is a number, repeated until boring, then repeated more — and ours is
   *provable* from baselines: every deal ships normal→today; the site runs an honest
   cumulative savings counter.
5. Engineered FOMO with receipts, sparingly and truthfully.
6. Growth = press + forums + forwarding, not ads. One front-page moment beats a quarter
   of posting.
7. Price low, annual, de-risked (guarantee, founding price).
8. Say no to everything else (see D10).
