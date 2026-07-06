#!/usr/bin/env python
"""Push scan outcomes to a phone via ntfy.sh (free, no accounts).

Usage: python scripts/notify_ntfy.py summary.json
Env:   NTFY_TOPIC  — the topic to publish to (treat as a password:
                     anyone who knows it can read/write the topic)
       JOB_STATUS  — GitHub's ${{ job.status }} ("success"/"failure"/...)

Rules:
  - alerts fired      -> high-priority push with the top alert lines
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

    if summary and summary.get("alerts_fired"):
        alerts = summary["alerts_fired"]
        cheapest = min(alerts, key=lambda a: a["price"])
        title = (f"Flight alert: {cheapest['destination']} from "
                 f"{cheapest['price']} {cheapest['currency']}")
        lines = [
            f"[{a['type']}] {a['origin']}->{a['destination']} "
            f"{a['departure_date']}..{a['return_date']} "
            f"{a['price']} {a['currency']}"
            for a in sorted(alerts, key=lambda a: a["price"])[:8]
        ]
        if len(alerts) > 8:
            lines.append(f"... and {len(alerts) - 8} more")
        _push(topic, title=title, body="\n".join(lines),
              priority="high", tags="airplane,rotating_light")
    elif job_status != "success" or summary is None:
        _push(topic, title="Flight scan needs attention",
              body=f"job status: {job_status}; "
                   f"summary: {'missing' if summary is None else summary.get('status')}. "
                   f"Check the Actions run.",
              priority="default", tags="warning")
    else:
        print(f"quiet run (status={summary.get('status')}, 0 alerts) — no push")
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
