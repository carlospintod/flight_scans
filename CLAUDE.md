# CLAUDE.md — Vuelazo

This file replaces the previous corridor-tracker CLAUDE.md at repo root. (The
Spain–Nairobi tracker remains a living subsystem; its historical design notes move to
`docs/DESIGN.md` history if needed.)

## What this project is

**Vuelazo** (vuelazo.es) is a Spanish flight-deal membership: automated fare discovery
across MAD/BCN/VLC/ALC, per-route price baselines, human-approved deal alerts in Spanish,
a free delayed tier and a paid instant tier (€39/año list, €29 founding; one-time
12-month passes, no auto-renewal). It is the evolution of this repo's flight tracker:
scans → fare history → deal detection → alerts → paying members. Tagline: *Vuelazos
desde tu aeropuerto.* Hook: *El precio normal, demostrado — y el chollo, a tiempo.*

## Prime directive

**Every product decision is already made.** They live in `docs/DECISIONS.md` (what and
why), `docs/MVP-SPEC.md` (what to build, milestone by milestone), and
`docs/PRELAUNCH-90D.md` (when). Claude Code makes **implementation** choices only —
libraries, file layout, function design, migration mechanics — always within the
conventions below. If anything in those documents is ambiguous or contradictory at
implementation time: **stop and ask Carlos. Never resolve a product question by
assumption.** "Product question" includes: pricing, tier boundaries, deal thresholds'
*semantics* (their numeric defaults are config), copy/voice, brand, legal posture,
which sources are paid, and anything touching money or members.

## Non-negotiables (inherited and extended)

1. **The quota ledger is unbypassable.** Every metered external call goes through a
   `GuardedClient` with a charge-before-call spend event. Predicted = guaranteed upper
   bound. No new source is called until it has a ledger spec (pool, cost shape, reset
   behavior). This is the load-bearing organ of the whole business — a €50/mo company
   cannot have surprise bills.
2. **No alert without live verification.** Cached observations nominate; only a
   Google-family live confirmation publishes. Mistake-class needs two independent
   coverage families (`lib/confidence.py` semantics).
3. **Guardrails are day-one, not v2:** daily candidate cap, per-route cooldowns, dedup,
   rejection reasons. Detector sophistication is incremental; safety is not.
4. **Offline-first tests.** Every external adapter gets fixtures from real sample
   responses; `pytest -q` must pass with zero network. Keep the estimator-drift pattern:
   any cost preview shown in the web app must provably equal the Python planner.
5. **Defensive parsing** of all third-party payloads (Aviasales renames fields; RapidAPI
   proxies drift). Parse to typed structures at the adapter boundary.
6. **Secrets:** never in code or fixtures. Owner-managed keys via `/ops` →
   `source_credentials` (existing pattern); infra secrets (TURSO_*, SESSION_SECRET,
   STRIPE_*, RESEND_*, ANTHROPIC_*) via env / Actions secrets only.
7. **Money and members are sacred paths:** Stripe webhooks idempotent; entitlement
   transitions logged; suppression list honored before every send; List-Unsubscribe
   one-click headers on all bulk mail.
8. **Spanish output is product surface.** Deal drafts come from
   `templates/deal_draft_es.md` via the Anthropic API; the voice spec is in
   DECISIONS §D7. Template edits are commits, not runtime improvisation.

## Environment

- **OS:** Windows 11. Shell examples in PowerShell. Python via `python -m venv .venv`
  then `.\.venv\Scripts\Activate.ps1`; run tests as
  `.\.venv\Scripts\python.exe -m pytest -q`.
- **Stack:** Python 3.12 pipeline · Turso/libSQL (HTTP, autocommit; local SQLite
  fallback) · Next.js (App Router) on Vercel in `web/` · GitHub Actions cron ·
  Playwright Chromium for the free Google Flights source · ntfy.sh push.
- **New services this build introduces:** Stripe (Checkout one-time mode + webhooks +
  Tax; test mode until Phase-3 W13), Resend (plain email API + batch only — never the
  contacts-priced Marketing track), Telegram Bot API, Anthropic API (drafting).
- Paths, keys, and CI wiring follow the existing patterns in `scripts/` and
  `.github/workflows/`.

## Working agreement per session

- Work milestone by milestone (M0 → M4b) as specified in `docs/MVP-SPEC.md` §5. Do not
  start milestone N+1 while N's DoD is unmet.
- **Definition of done, universally:** the milestone's DoD checklist in MVP-SPEC §5,
  plus: all tests green offline; one real (or Stripe-test) end-to-end run demonstrated;
  ledger receipts shown for any metered calls; no TODOs on money/member paths; short
  `docs/notes/M<N>.md` recording implementation choices made and why.
- Prefer evolving existing modules over parallel new ones (e.g., generalize
  `lib/alerts.py`; extend `/ops`). Delete dead code as subsystems are superseded —
  including the retiring Streamlit UI when the ops queue replaces its last use.
- Budget stance: build phase runs on the €0 stack. Flipping any paid tier (SearchAPI
  Developer, Resend Pro) is **Carlos's explicit action**, never a code default.
- When a design choice is genuinely open (chart library, PNG renderer, cron worker vs
  Actions), pick the boring option that a solo maintainer can debug at 23:00 on a
  Tuesday, and record it in the milestone notes.

## First session

Read `docs/DECISIONS.md` and `docs/MVP-SPEC.md` fully. Then build **M0** — the walking
skeleton — exactly as specified: one origin, one candidate, one verified deal, one
draft, one approval, one Telegram post, one email. Nothing else until that runs.
