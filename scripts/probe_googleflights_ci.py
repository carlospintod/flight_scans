#!/usr/bin/env python
"""Probe: does the free Google Flights scraper work from THIS host's IP?

Runs 3 production-path point queries (lib.googleflights_direct — the
same client scans use, not a lookalike) with dynamically computed
in-window dates. No secrets, no DB.

Outputs:
  gf_probe/verdict.json    {queries: [...], parsed_total, verdict}
  gf_probe/fail_N.html     rendered DOM of any query that parsed nothing

Exit 0 iff >= 2 of 3 queries parsed >= 1 flight option ("OK"), else 1
("BLOCKED") — the workflow run's green/red IS the verdict. Intended for
GitHub Actions (datacenter IPs may get consent walls or captchas that a
residential IP doesn't); also useful to re-check monthly since Google's
posture drifts.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

OUT_DIR = Path("gf_probe")


def main() -> int:
    from lib.googleflights_direct import GoogleFlightsClient, is_available

    OUT_DIR.mkdir(exist_ok=True)
    if not is_available():
        verdict = {"queries": [], "parsed_total": 0, "verdict": "BLOCKED",
                   "reason": "playwright/chromium not installed"}
        (OUT_DIR / "verdict.json").write_text(json.dumps(verdict, indent=2))
        print("BLOCKED: browser stack not installed")
        return 1

    today = date.today()
    # In-horizon, stay-realistic pairs (~70-130d out, 68-78d stays).
    probes = [
        ("MAD", "NBO", today + timedelta(days=70), today + timedelta(days=145)),
        ("MAD", "NBO", today + timedelta(days=100), today + timedelta(days=168)),
        ("BCN", "NBO", today + timedelta(days=85), today + timedelta(days=163)),
    ]
    queries = []
    with GoogleFlightsClient() as client:
        for i, (origin, dest, dep, ret) in enumerate(probes):
            dump = OUT_DIR / f"fail_{i}.html"
            entry = {"origin": origin, "destination": dest,
                     "departure": dep.isoformat(), "return": ret.isoformat(),
                     "options": 0, "cheapest": None, "error": None}
            try:
                resp = client.point_query(
                    origin=origin, destination=dest,
                    outbound=dep, return_=ret, dump_html_to=dump)
                entry["options"] = len(resp.best_flights)
                if resp.best_flights:
                    best = resp.best_flights[0]
                    entry["cheapest"] = {"price": best.price,
                                         "carriers": best.carriers,
                                         "stops": best.stops}
                    dump.unlink(missing_ok=True)  # keep DOM only on failure
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)[:500]
            queries.append(entry)
            print(f"probe {origin}->{dest} {dep}..{ret}: "
                  f"{entry['options']} options"
                  + (f" (cheapest {entry['cheapest']['price']})"
                     if entry["cheapest"] else "")
                  + (f" ERROR: {entry['error']}" if entry["error"] else ""))

    ok_count = sum(1 for q in queries if q["options"] >= 1)
    verdict = {
        "queries": queries,
        "parsed_total": sum(q["options"] for q in queries),
        "ok_queries": ok_count,
        "verdict": "OK" if ok_count >= 2 else "BLOCKED",
    }
    (OUT_DIR / "verdict.json").write_text(json.dumps(verdict, indent=2),
                                          encoding="utf-8")
    print(f"\nVERDICT: {verdict['verdict']} ({ok_count}/3 queries parsed)")
    print("Set the repo Actions variable GF_ON_ACTIONS to "
          + ("'true'" if verdict["verdict"] == "OK" else "'false'")
          + " accordingly.")
    return 0 if verdict["verdict"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
