# flight-tracker

A configurable price tracker for flexible-date round-trip flights. Scans a date
rectangle across a target window, tracks how prices evolve over time, and alerts
on drops below a per-itinerary baseline.

First run targets **Spain (MAD, BCN) ↔ Nairobi (NBO)**, but everything
route-specific lives in `routes/<name>.yaml` — same code runs for any other
flexible-dates corridor by swapping the config file.

See [`CLAUDE.md`](CLAUDE.md) for the full design doc, data-source notes,
architecture decisions, and build order.

## Two data sources

| Source | Free tier | Strength |
|---|---|---|
| **SearchAPI.io** (Google Flights wrapper) | 100 calls/mo | (departure × return) rectangle pricing |
| **Sky Scrapper** (RapidAPI / Skyscanner wrapper) | 100 calls/mo | Year-long departure curve in one call; surfaces virtual interlining via `isSelfTransfer` |

Both run side-by-side. The DB tags every row with its `source` so per-source baselines, comparisons, and budget tracking work cleanly.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1     # PowerShell on Windows
pip install -r requirements.txt

cp .env.example .env             # then add both keys to .env:
                                 #   SEARCHAPI_KEY=<your SearchAPI key>
                                 #   RAPIDAPI_KEY=<your RapidAPI key>

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
