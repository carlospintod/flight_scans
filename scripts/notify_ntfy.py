#!/usr/bin/env python
"""Push scan outcomes to a phone via ntfy.sh (free, no accounts).

Usage: python scripts/notify_ntfy.py summary.json
Env:   NTFY_TOPIC  — the topic to publish to (treat as a password:
                     anyone who knows it can read/write the topic)
       JOB_STATUS  — GitHub's ${{ job.status }} ("success"/"failure"/...)

Rules:
  - alerts fired      -> high-priority push with the top alert lines
  - source health     -> push each pre-computed transition alert (a source
                          went dark / payment-walled, a whole role lost all
                          live sources, a scan stored 0 rows, chronic skips).
                          run_batch computes these (it has the DB + prior
                          state); this script stays a dumb pusher so it
                          needs no DB access in CI.
  - job failed / no summary -> ops ping so a broken pipeline is noticed
  - quiet run         -> no push (2-3 scans a week must not train
                          notification blindness)
Never exits non-zero — a notification hiccup must not redden a good scan.
"""

from __future__ import annotations

import json
import os
import sys

import requests


def main() -> int:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC not set — skipping notification")
        return 0
    job_status = os.environ.get("JOB_STATUS", "unknown")

    summary = None
    try:
        summary = json.load(open(sys.argv[1], encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"no summary ({exc})")

    pushed = False

    # Price alerts.
    if summary and summary.get("alerts_fired"):
        alerts = summary["alerts_fired"]
        cheapest = min(alerts, key=lambda a: a["price"])
        # Flag ONE-WAY explicitly: an empty return_date used to render as a
        # bare "2026-12-16.." that looked like a round-trip with a missing
        # leg (2026-07-15: an owner got a one-way €478 alert and couldn't
        # find it on the round-trip radar). Say "one-way" so it's obvious
        # which search (round-trip vs one-way) the fare belongs to.
        oneway = not cheapest.get("return_date")
        title = (f"Flight alert: {cheapest['destination']}"
                 f"{' one-way' if oneway else ''} from "
                 f"{cheapest['price']} {cheapest['currency']}")
        lines = []
        for a in sorted(alerts, key=lambda a: a["price"])[:8]:
            trip = (f"one-way {a['departure_date']}"
                    if not a.get("return_date")
                    else f"{a['departure_date']}..{a['return_date']}")
            lines.append(f"[{a['type']}] {a['origin']}->{a['destination']} "
                         f"{trip} {a['price']} {a['currency']}")
        if len(alerts) > 8:
            lines.append(f"... and {len(alerts) - 8} more")
        _push(topic, title=title, body="\n".join(lines),
              priority="high", tags="airplane,rotating_light")
        pushed = True

    # Source-health alerts (pre-computed by run_batch; transition-based).
    for h in (summary or {}).get("health_alerts", []):
        _push(topic, title=h.get("title", "Source health"),
              body=h.get("body", ""),
              priority=h.get("priority", "default"),
              tags=h.get("tags", "warning"))
        pushed = True

    # Broken pipeline.
    if job_status != "success" or summary is None:
        _push(topic, title="Flight scan needs attention",
              body=f"job status: {job_status}; "
                   f"summary: {'missing' if summary is None else summary.get('status')}. "
                   f"Check the Actions run.",
              priority="default", tags="warning")
        pushed = True

    if not pushed:
        print(f"quiet run (status={summary.get('status')}, 0 alerts, "
              f"healthy) — no push")
    return 0


def _push(topic: str, *, title: str, body: str, priority: str, tags: str) -> None:
    try:
        r = requests.post(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": tags},
            timeout=15,
        )
        print(f"ntfy push: HTTP {r.status_code}")
    except requests.RequestException as exc:
        print(f"ntfy push failed (non-fatal): {exc}")


if __name__ == "__main__":
    sys.exit(main())
