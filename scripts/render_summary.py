#!/usr/bin/env python
"""Render run_scan's --json-summary as GitHub Actions job-summary markdown.

Usage: python scripts/render_summary.py summary.json >> $GITHUB_STEP_SUMMARY
Never fails the job: any problem renders as a note instead of an error.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    # Emoji in the output; Windows consoles default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        path = sys.argv[1]
        s = json.load(open(path, encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"> summary unavailable ({exc}) — scan likely failed before "
              f"writing it; see the run log.")
        return 0

    status_icon = {"ok": "🟢", "degraded": "🟡"}.get(s.get("status"), "🔴")
    if "per_search" in s:  # batch shape (run_batch.py)
        print(f"## {status_icon} Batch scan ({s.get('trigger')}): "
              f"{s.get('searches_total', 0) - s.get('searches_skipped', 0)}"
              f"/{s.get('searches_total', 0)} searches ran, "
              f"{s.get('searches_skipped', 0)} skipped")
        print()
        print(f"`{s.get('started_at')}` → `{s.get('finished_at')}`")
        print()
        print("| Search | Status | Rows | Alerts | Reserved → used |")
        print("|--------|--------|------|--------|-----------------|")
        for ps in s.get("per_search", []):
            rvu = "; ".join(
                f"{r['source']}{'*' if r['kind'] == 'contingency' else ''} "
                f"≤{r['reserved_units']}→{r['used_units']}"
                for r in ps.get("reserved_vs_used", []))
            print(f"| {ps['search_id']} | {ps['status']} "
                  f"| {ps['rows_stored']} | {ps['alerts']} | {rvu or '—'} |")
        print()
        pools = s.get("pools") or {}
        if pools:
            print("### Pools after run")
            print()
            for src, p in pools.items():
                print(f"- {src}: available {p.get('available')}")
            print()
        alerts = s.get("alerts_fired") or []
        print(f"### Alerts fired: {len(alerts)}")
        for a in alerts[:10]:
            print(f"- **{a['type']}** {a['origin']}→{a['destination']} "
                  f"{a['departure_date']}..{a['return_date']} "
                  f"at **{a['price']} {a['currency']}**")
        return 0

    print(f"## {status_icon} Scan: {s.get('route')} "
          f"({s.get('trigger')}, config: {s.get('config_source')})")
    print()
    print(f"`{s.get('started_at')}` → `{s.get('finished_at')}`")
    print()

    cheapest = s.get("cheapest_top5") or []
    if cheapest:
        print("### Cheapest in window")
        print()
        print("| # | Route | Depart | Return | Stay | Price |")
        print("|---|-------|--------|--------|------|-------|")
        for i, c in enumerate(cheapest, 1):
            print(f"| {i} | {c['origin']}→{c['destination']} "
                  f"| {c['departure_date']} | {c['return_date']} "
                  f"| {c['stay_days']}d | **{c['price']} {c['currency']}** |")
        print()

    alerts = s.get("alerts_fired") or []
    print(f"### Alerts fired: {len(alerts)}")
    if alerts:
        print()
        for a in alerts[:10]:
            print(f"- **{a['type']}** {a['origin']}→{a['destination']} "
                  f"{a['departure_date']}..{a['return_date']} "
                  f"at **{a['price']} {a['currency']}**")
        if len(alerts) > 10:
            print(f"- … and {len(alerts) - 10} more")
    print()

    results = s.get("results") or {}
    if results:
        print("### Sources")
        print()
        print("| Source | Attempted | Stored | Error |")
        print("|--------|-----------|--------|-------|")
        for src, r in results.items():
            err = (r.get("error") or "—")[:80]
            print(f"| {src} | {r.get('attempted', 0)} | {r.get('stored', 0)} "
                  f"| {err} |")
        print()

    for w in s.get("client_warnings") or []:
        print(f"> ⚠ {w}")
    for n in s.get("plan_notes") or []:
        print(f"> {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
