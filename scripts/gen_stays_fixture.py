#!/usr/bin/env python
"""Generate the sampled-stays drift-guard fixture.

Python owns StayPreferences.sampled() (lib/config.py); the web radar
mirrors it in web/src/lib/stays.ts. `node web/scripts/check-stays.mjs`
re-computes these cases through the TS file and fails on any divergence.

Usage: python scripts/gen_stays_fixture.py > web/stays-fixture.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.config import StayPreferences  # noqa: E402


def main() -> int:
    cases = []
    for lo, hi, step in [(30, 90, 7), (60, 90, 1), (30, 30, 7), (30, 37, 7),
                         (1, 14, 3), (7, 21, 5), (0, 10, 2), (10, 5, 1),
                         (45, 90, 14), (2, 3, 10)]:
        cases.append({"input": {"min": lo, "max": hi, "step": step},
                      "expected": list(StayPreferences(lo, hi, step).sampled())})
    json.dump({"cases": cases}, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
