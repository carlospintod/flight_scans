# flight-tracker

A configurable price tracker for flexible-date round-trip flights. Scans a date
rectangle across a target window, tracks how prices evolve over time, and alerts
on drops below a per-itinerary baseline.

First run targets **Spain (MAD, BCN) ↔ Nairobi (NBO)**, but everything
route-specific lives in `routes/<name>.yaml` — same code runs for any other
flexible-dates corridor by swapping the config file.

See [`CLAUDE.md`](CLAUDE.md) for the full design doc, data-source notes,
architecture decisions, and build order.

## Quick start

```bash
python -m venv .venv
. .venv/Scripts/activate   # Windows; use .venv/bin/activate on Unix
pip install -r requirements.txt

cp .env.example .env       # then put SEARCHAPI_KEY in .env

python tracker.py sweep    --route spain-nairobi
python tracker.py followup --route spain-nairobi
python tracker.py alerts   --route spain-nairobi
python tracker.py report   --route spain-nairobi
```

## Layout

```
tracker.py                # CLI entry point
routes/<name>.yaml        # one config file per tracked route
lib/                      # config, api, db, sweep, followup, alerts, report
data/tracker.db           # SQLite store (gitignored)
tests/                    # unit tests; fixtures live under tests/fixtures/
```
