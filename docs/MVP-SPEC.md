# Vuelazo — MVP Specification

Repo destination: `docs/MVP-SPEC.md`
All product decisions referenced here are closed in `docs/DECISIONS.md`. This spec
defines *what* to build; Claude Code owns *how*, within the repo's existing conventions.

---

## 1. Architecture: the delta from flight_scans

flight_scans (corridor tracker) becomes one subsystem of Vuelazo (deal product). The
quota ledger, guarded clients, source adapters, confidence families, Turso storage,
Actions cron, Next.js app, and ops auth all survive and carry the new system.

**New capabilities to build:**

| # | Capability | Builds on |
|---|---|---|
| 1 | Origin→anywhere discovery (Travelpayouts origin-only sweeps; Skyscanner-proxy "everywhere" probe) | `lib/aviasales_api.py`, probe scripts |
| 2 | Route watchlist + per-route baselines + deal gate + cross-route score | `lib/alerts.py` generalized |
| 3 | Deal pipeline: candidate → verify → score → draft → queue → publish | runner + ledger |
| 4 | Claude drafting (Anthropic API) from versioned template | new `lib/drafting.py`, `templates/` |
| 5 | Ops deal queue UI (cards, approve / reject-with-reason / edit) | existing `/ops` |
| 6 | Publish fan-out: Telegram (private + public delayed), Resend email, site archive | new adapters |
| 7 | Membership: Stripe one-time pass, entitlements, magic links, Telegram binding | existing auth + web app |
| 8 | Digest assembler (weekly, from approvals) | pipeline data |
| 9 | Deal-card renderer (1080×1350 PNG from deal data + tokens) | design system |
| 10 | Public site pages + gated SEO route pages (SSG nightly) | existing components |

**Cadence (per D1/D2):** anywhere sweep 3–4×/day per origin (free); watchlist refresh
1×/day (free); paid verification candidate-capped (≤15/day + mistake fast lane). Cron on
GitHub Actions initially; note jitter (15–60 min under load) is acceptable for standard
deals — a ~€5/mo always-on worker is the punctuality upgrade if launch data shows fast
movers dying in the gap (implementation call, ledger-metered either way).

## 2. Data model (additions — schema sketch; Claude Code finalizes DDL/migrations)

- `routes_watchlist(origin, dest, active, added_at, seeded_since, obs_count, route_class)`
  — route_class ∈ {intra_eu, medium, long} drives floors and aspiration weights.
- `fare_observations(id, origin, dest, depart_date, return_date, price, currency, source,
  source_family, found_at, observed_at, is_verified)` — unified store for cached and
  live observations; baselines query verified rows.
- `deals(id, origin, dest, depart_window, return_window, sample_dates, price,
  baseline_median, baseline_p10, pct_below, abs_saving, score, class{standard|mistake},
  status{candidate|verified|queued|approved|rejected|expired|published}, draft_es,
  draft_version, verification_refs, confidence, created_at, approved_at, published_at,
  publish_targets)`
- `rejections(deal_id, reason{too_common|bad_dates|ulcc_junk|thin_saving|other}, note,
  ts)` — the tuning signal (D2/D3).
- `members(id, email, status{active|lapsed|refunded}, member_until, plan{founding|list},
  price_paid, stripe_customer_id, stripe_payment_ref, telegram_user_id, airports[],
  created_at)` — entitlement model; one-time 12-month passes (D5).
- `send_log(id, member_id|null, channel{email|tg_private|tg_public}, deal_id|digest_id,
  ts, provider_ref)` + `suppressions(email, reason, ts)` — List-Unsubscribe obligations.
- `seo_pages(origin, dest, status{published|noindex}, last_generated, intro_es,
  intro_generated_at)`
- Reuse `spend_events` / ledger tables unchanged.

## 3. Pipeline (the daily machine)

1. **Discover** (free): origin-only Aviasales sweeps + watchlist refresh → upsert
   `fare_observations` (cached, `is_verified=false`).
2. **Gate** (D2, incremental): absolute route-class floor + same-day cross-section;
   percentile gate activates per route at `min_observations`. Cooldowns, dedup,
   daily cap applied here. Cached-only rows can *nominate*, never alert.
3. **Verify** (paid, ledger-reserved): one Google-family live check per surviving
   candidate; mistake-class requires a second independent family. Failures die quietly.
4. **Score & draft:** cross-route score; Claude draft via Anthropic API from
   `templates/deal_draft_es.md` (voice spec in DECISIONS D7). Drafts are versioned;
   template edits are commits.
5. **Queue:** ops console cards (draft, sparkline, confidence, verification link,
   score). Mistake-class → ntfy push immediately.
6. **Publish** (on approve): fan-out per free/paid line — private Telegram + member
   emails (per-airport filtering) instantly; public Telegram + archive at T+24h for the
   1–3/week free picks (approver flags which); site archive entry always.
7. **Digest:** Sunday assembler drafts from the week's approvals → one review → send to
   free list.

Config lives in YAML seeds + DB (existing pattern). Every external call goes through
guarded clients; every metered source has a ledger spec **before** first call.

## 4. Site scope (page-by-page)

Public: `/` landing (hook, live savings counter, latest delayed deals, free signup,
founder story block) · `/vuelazos` public archive (24h-delayed, per-airport filter) ·
`/vuelos-baratos/[origin]` ×4 hub pages · `/vuelos-baratos/[origin]/[dest]` gated SEO
pages (D6; SSG nightly; noindex until gate passes) · `/unete` checkout (€39 / €29
founding, one-time 12-month, Bizum + cards, IVA-included pricing, 14-day guarantee, "sin
renovación automática" copy) · `/gracias` (Telegram deep link + what-happens-next) ·
legal: aviso legal, privacidad, condiciones (gestor-reviewed).
Member: `/cuenta` (airports preference, Telegram link status, membership expiry, renew
CTA, unsubscribe granularity). Magic-link login (existing pattern; email via Resend).
Ops (extend existing): deal queue, template version pointer, metrics panel (§6), free-
pick flagging, member admin.

## 5. Milestones — walking skeleton first; every milestone runnable & verifiable

**M0 — Walking skeleton (one route end-to-end before any UI).**
VLC origin-only sweep → floor gate → 1 live verification (SerpAPI free) → score → Claude
draft → console approve → post to *private test* Telegram channel + email to Carlos via
Resend.
*DoD:* one command (`run_deals.py`) does the whole chain; offline fixtures for every new
adapter; one real run with ledger receipt showing used ≤ reserved; draft renders the
voice spec fields.

**M1 — Four origins, real detector.**
Watchlist (~35×4), baselines accumulating, incremental gate activation, cooldowns/caps/
dedup, rejection reasons, ops queue UI with cards.
*DoD:* 7 consecutive green daily cron runs; queue never exceeds 15; all observations
persisted and queryable; week-1 audits produced (cache freshness per route via
`found_at` + `market=es`; `price_insights` coverage report; Skyscanner-everywhere probe
verdict).

**M2 — Membership.**
Stripe Checkout one-time pass (both prices), webhook → member row, magic-link accounts,
Telegram bind + private-channel admission, lapse removal, per-airport alert filtering,
T-30/T-7 renewal reminders, refund handling (14-day), List-Unsubscribe + suppressions.
*DoD:* full lifecycle in Stripe test mode with test clocks — purchase → admitted →
filtered alert received → lapse → removed; Bizum path exercised in test.

**M3 — Publishing polish.**
Digest assembler; T+24h free-channel automation with approver free-pick flag; deal-card
renderer (PNG, brand tokens); seed-list deliverability test executed and documented.
*DoD:* one fully simulated week produces: N member alerts, free picks at T+24h, Sunday
digest, ≥3 deal cards — all from real pipeline data.

**M4a — Public site (required for launch).**
Landing, archive, checkout, cuenta, legal, hubs. SPF/DKIM/DMARC live.
*DoD:* production deploy on vuelazo.es; Lighthouse ≥90 perf/a11y on landing; checkout
test purchase end-to-end in live-mode-test.

**M4b — SEO route pages (sacrificial; cut first if timeline slips, per D6).**
Gated generation, nightly SSG, Claude intros, sitemap.
*DoD:* only gate-passing routes indexed; spot-check 10 pages read non-templated.

## 6. Ops runbook & metrics (the 10-minute contract)

Daily: open queue → approve/reject ≤15 cards → done. Sunday: digest review. Weekly
metrics glance: candidate yield per origin; VLC/ALC excellent-deal rate (D0b checkpoint
feed); cache freshness; ledger spend vs ceiling; deliverability (bounce/complaint);
post-launch adds: free subs, conversion %, refunds, renewal rate.

## 7. Cost ceilings (ledger-enforced)

Build phase €0 (+domain). Audience phase: Resend Pro ~€19 from ~100 subs. Launch:
SearchAPI ~€37 + drafting ~€4 + Resend ~€19 ≈ €60/mo — flex over the €50 soft ceiling
blessed in session. Anything beyond requires a new decision, not a bigger plan.
