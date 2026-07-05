# flight-tracker

A configurable price tracker for flexible-date round-trip flights. Scans a date
rectangle across a target window, tracks how prices evolve over time, and alerts
on drops below a per-itinerary baseline.

First run targets **Spain (MAD, BCN) ↔ Nairobi (NBO)**, but everything
route-specific lives in `routes/<name>.yaml` — same code runs for any other
flexible-dates corridor by swapping the config file.

See [`CLAUDE.md`](CLAUDE.md) for the full design doc, data-source notes,
architecture decisions, and build order.

## Data sources (free, sustainable)

| Source | Cost | Role |
|---|---|---|
| **Google Flights direct** (`googleflights`) | FREE, unmetered at polite volume | Primary verification: local headless Chromium renders Google Flights pages, parses aria-labels (price/carrier/stops/duration). Needs `playwright install chromium` locally; auto-disabled on Streamlit Cloud |
| **Kiwi** (RapidAPI, `kiwi`) | 300/mo, resets ~10th | Discovery engine: one range-search call sweeps a multi-week departure band and returns the cheapest ~50 itineraries with carriers + virtual-interlining flags |
| **Aviasales** (Travelpayouts, `aviasales`) | soft-unlimited | Bonus signal (Saudia + MENA carriers Google skips); cache sparse on some corridors |
| **Sky Scrapper** (RapidAPI, `skyscanner`) | 20/mo, resets ~16th | Monthly seasonal departure-price curve |
| **SearchAPI.io** (`searchapi`) | free credits are ONE-TIME (~100 at signup) | Break-glass verification only — reserve the last credits for booking day |

Every DB row is tagged with its `source`; baselines, alerts, and budget tracking are per-source. Alerts fire on 15% drops vs the 30-day median AND on any **new all-time low** (needs only 2 scans).

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1     # PowerShell on Windows
pip install -r requirements.txt
playwright install chromium      # for the free googleflights source

cp .env.example .env             # then add keys to .env:
                                 #   RAPIDAPI_KEY=<RapidAPI key: kiwi + sky scrapper>
                                 #   TRAVELPAYOUTS_TOKEN=<travelpayouts token>
                                 #   SEARCHAPI_KEY=<optional, break-glass>

# Recommended: the one-shot scan (plan -> discover -> verify -> alert)
python run_scan.py --sources googleflights,aviasales,kiwi

# CLI mode
python tracker.py sweep    --route spain-nairobi
python tracker.py followup --route spain-nairobi
python tracker.py alerts   --route spain-nairobi
python tracker.py report   --route spain-nairobi

# Dashboard mode
streamlit run ui/app.py          # opens http://localhost:8501
```

`--sources searchapi skyscanner` controls which sources to query on each CLI invocation; default is both. `--dry-run` plans without spending API budget.

## Layout

```
tracker.py                # CLI entry point
routes/<name>.yaml        # one config file per tracked route
lib/                      # business logic
  config.py               #   YAML loader + validator
  searchapi_io.py         #   SearchAPI.io client (Google Flights wrapper)
  skyscanner_rapidapi.py  #   Sky Scrapper client (Skyscanner wrapper)
  db.py                   #   SQLite schema + queries
  sweep.py                #   Tier 1: curve + grid discovery
  followup.py             #   Tier 2: point queries (both sources)
  alerts.py               #   per-source drop detection
  report.py               #   terminal report
ui/
  app.py                  # Streamlit landing page
  _common.py              # shared chart/DB helpers
  pages/                  # Run jobs · Itinerary detail · Alerts
data/tracker.db           # SQLite store (gitignored)
tests/                    # fixture-driven unit tests (34, network-free)
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
