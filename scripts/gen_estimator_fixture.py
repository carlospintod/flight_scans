#!/usr/bin/env python
"""Generate the estimator drift-guard fixture.

Python owns predict_upper_bounds (lib/planner.py); the web form mirrors
it in web/src/lib/predict.ts. This script emits deterministic cases from
the PYTHON side; `node web/scripts/check-estimator.mjs` re-computes them
in TS-land and fails CI on ANY divergence — formula changes must land on
both sides plus nothing else.

Usage: python scripts/gen_estimator_fixture.py > web/estimator-fixture.json
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.planner import predict_upper_bounds  # noqa: E402


def main() -> int:
    cases = []
    base = date(2026, 9, 12)
    grid = [
        # (n_origins, n_dest, window_days_total, min_stay)
        (1, 1, 30, 7), (1, 1, 60, 30), (2, 1, 125, 60), (1, 1, 1, 1),
        (2, 2, 90, 14), (1, 1, 365, 90), (3, 1, 21, 21), (1, 1, 22, 7),
        (1, 1, 42, 10), (2, 1, 63, 5),
    ]
    for n_o, n_d, span, min_stay in grid:
        earliest = base
        latest_return = base + timedelta(days=span)
        cases.append({
            "input": {
                "nOrigins": n_o, "nDestinations": n_d,
                "earliestDeparture": earliest.isoformat(),
                "latestReturn": latest_return.isoformat(),
                "minStayDays": min_stay,
            },
            "expected": predict_upper_bounds(
                n_origins=n_o, n_destinations=n_d,
                earliest_departure=earliest, latest_return=latest_return,
                min_stay_days=min_stay,
            ),
        })
    print(json.dumps({"cases": cases}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
