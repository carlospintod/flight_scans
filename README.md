# flight_scans

A flexible-date flight price tracker with a search mechanism mainstream
tools don't offer: **both your departure and your return float freely
inside a window, bounded by a min/max trip length.** The tracker hunts
the cheapest `(departure, return)` combination across thousands of date
pairs, three times a week, on a $0/month stack — and tells you the exact
API budget of every search **before** it runs, as a guaranteed upper
bound.

First corridor: **Spain (MAD/BCN) → Nairobi (NBO)** — live as the public
demo on the web app. The system is multi-user (invite-only) and fully
route-agnostic.

## Architecture

```
GitHub Actions cron (Mon/Wed/Sat)          Vercel (Next.js 16)
┌───────────────────────────────┐          ┌────────────────────────────┐
│ run_batch.py                  │          │ /            public demo   │
│  for each active search:      │          │ /searches    your searches │
│   plan → quote → RESERVE      │─ Turso ─▶│ /searches/new + cost       │
│   → execute (guarded clients) │  libSQL  │              preview       │
│   → settle (reserved vs used) │          │ /s/[slug]    results       │
│   skip-and-notify on shortfall│          │ /ops         owner console │
└───────────────────────────────┘          └────────────────────────────┘
```

- **Quota ledger** (`lib/quota.py`): charge-before-call spend events, one
  per HTTP attempt; provider headers re-anchor pools; reservations are
  single-statement compare-and-swap (no transactions needed on Turso's
  autocommit HTTP API). *Predicted = guaranteed upper bound* — a run can
  spend less than quoted, never more; searches that don't fit are
  skipped with a recorded reason, never silently degraded.
  **This is the core of the project — the full design is in
  [docs/ENGINE.md](docs/ENGINE.md)**: how to shadow quota meters you
  don't own, reserve whole jobs against them with lock-free CAS, and
  survive resets you're never told about. Nothing in it is
  flight-specific.
- **Auth**: signed one-time links (invite = login), hand-rolled HMAC
  sessions, no passwords, no email dependency, users in our own DB.
- **Alerts**: 15% drops vs the 30-day per-itinerary median AND new
  all-time lows (fire from the second scan). Push via ntfy.sh.

## Data sources (all free)

| Source | Budget | Role |
|---|---|---|
| **Google Flights direct** | free, politeness-bounded | Verification: headless Chromium on the CI runner parses public result pages (probed: works from Actions IPs) |
| **Kiwi** (RapidAPI) | 300/mo free → $5/mo for 20k (a config switch) | Discovery: one range-search sweeps a multi-week band, ~50 cheapest itineraries |
| **SerpAPI** | 250/mo free | Managed verification — the contingency rail when the browser dies |
| **Aviasales** (Travelpayouts) | soft-unlimited (cached 2-7d) | Broad cached sweep; carriers Google skips |

Both trip types ride the full stack: round-trip and one-way each get
Kiwi discovery, Google Flights verification, the SerpAPI contingency,
and Aviasales corroboration — with per-source upper bounds quoted at
creation time.
| **SearchAPI.io** | 2 one-time credits left | Local break-glass for booking day; never in CI |

## Quick start (local development)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium          # for the free googleflights source

cp .env.example .env                 # add: RAPIDAPI_KEY, TRAVELPAYOUTS_TOKEN,
                                     # SERPAPI_KEY, TURSO_* (optional: local
                                     # SQLite is the fallback)

python run_batch.py --trigger local  # all active searches, quota-enforced
python run_scan.py --route spain-nairobi   # legacy single-route path

cd web && npm install && npm run dev # the web app on localhost:3000
```

CI runs `run_batch.py` on the cron; repo secrets carry the keys
(`scripts/set_ci_secrets.py` pushes them from `.env`). The estimator
drift guard (`web/scripts/check-estimator.mjs`) keeps the web cost
preview provably equal to the Python planner's geometry.

## Layout

```
run_batch.py             # multi-search batch runner (what CI executes)
run_scan.py              # legacy single-route runner (local fallback)
routes/<name>.yaml       # seed config (DB is the source of truth)
docs/ENGINE.md           # the quota engine design — start here
lib/                     # planner, quota ledger, source clients,
                         # alerts, scan ops, route store
scripts/                 # CI probes, summaries, notifications, secrets
web/                     # Next.js app (Vercel): demo, searches, ops
ui/                      # legacy Streamlit dashboard (retiring)
tests/                   # 165+ offline fixture-driven tests
```

## Honest limits

Not a booking site — prices are observations from free sources and can
be hours old; verify before paying. Some sources scrape public pages
politely (tens of queries per scan). Free-tier capacity is deliberately
small (the owner + a couple of guest searches) until the $5 Kiwi switch
flips. Saudia doesn't appear in Google Flights on the MAD-NBO corridor —
a known blind spot.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node web/scripts/check-estimator.mjs
```
