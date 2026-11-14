#!/usr/bin/env python
"""Push scan outcomes to a phone via ntfy.sh (free, no accounts).

Usage: python scripts/notify_ntfy.py summary.json
Env:   NTFY_TOPIC  — the topic to publish to (treat as a password:
                     anyone who knows it can read/write the topic)
       JOB_STATUS  — GitHub's ${{ job.status }} ("success"/"failure"/...)

Rules:
  - alerts fired      -> high-priority push with the top alert lines; a
                          window_best alert (a new best verified fare for
                          the search's window) leads it and names the
                          previous best in the title
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
        title, body = price_push(summary["alerts_fired"])
        _push(topic, title=title, body=body,
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


# lib.alerts.WINDOW_BEST. Not imported: this script runs as
# `python scripts/notify_ntfy.py` in CI, where `lib` is not on sys.path.
WINDOW_BEST = "window_best"
MAX_LINES = 8


def price_push(alerts: list[dict]) -> tuple[str, str]:
    """(title, body) of the price-alert push for summary['alerts_fired'].

    A window_best alert leads: the title names the new best and the one
    it beat ("New best NBO fare: 549 EUR (was 557)"), and its line comes
    first. Without one, the title is the cheapest alert, as before.
    `ref_price` may be missing from summaries written before 2026-11-14.
    """
    # Flag ONE-WAY explicitly, in the title and the lines: an empty
    # return_date used to render as a bare "2026-12-16.." that looked like
    # a round-trip with a missing leg (2026-07-15: an owner got a one-way
    # €478 alert and couldn't find it on the round-trip radar). Say
    # "one-way" so it's obvious which search the fare belongs to.
    bests = [a for a in alerts if a.get("type") == WINDOW_BEST]
    if bests:
        head = min(bests, key=lambda a: a["price"])
        oneway = not head.get("return_date")
        was = (f" (was {head['ref_price']})"
               if head.get("ref_price") is not None else "")
        title = (f"New best {head['destination']}"
                 f"{' one-way' if oneway else ''} fare: "
                 f"{head['price']} {head['currency']}{was}")
    else:
        cheapest = min(alerts, key=lambda a: a["price"])
        oneway = not cheapest.get("return_date")
        title = (f"Flight alert: {cheapest['destination']}"
                 f"{' one-way' if oneway else ''} from "
                 f"{cheapest['price']} {cheapest['currency']}")
    lines = []
    ordered = sorted(alerts, key=lambda a: (a.get("type") != WINDOW_BEST,
                                            a["price"]))
    for a in ordered[:MAX_LINES]:
        trip = (f"one-way {a['departure_date']}"
                if not a.get("return_date")
                else f"{a['departure_date']}..{a['return_date']}")
        line = (f"[{a['type']}] {a['origin']}->{a['destination']} "
                f"{trip} {a['price']} {a['currency']}")
        if a.get("type") == WINDOW_BEST and a.get("ref_price") is not None:
            line += f" (best was {a['ref_price']})"
        lines.append(line)
    if len(alerts) > MAX_LINES:
        lines.append(f"... and {len(alerts) - MAX_LINES} more")
    return title, "\n".join(lines)


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
        # The topic is a password and sits in the URL that str(exc) holds.
        # Inline, not lib/credentials: this script stays a dumb pusher.
        detail = str(exc).replace(topic, "REDACTED")
        print(f"ntfy push failed (non-fatal): {type(exc).__name__}: {detail}")


if __name__ == "__main__":
    sys.exit(main())
