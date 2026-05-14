"""Terminal report: cheapest itineraries by month + recent alerts."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from .config import RouteConfig
from .db import cheapest_recent_itineraries, recent_alerts


def print_report(*, conn: sqlite3.Connection, route: RouteConfig, limit: int = 20) -> None:
    print(f"== {route.name} ==")

    rows = cheapest_recent_itineraries(
        conn,
        route.name,
        min_stay=route.stay.min_days,
        max_stay=route.stay.max_days,
        limit=limit * 6,  # pull extra so we can show some per-month variety
    )
    by_month: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_month[r["departure_date"][:7]].append(r)

    print()
    print("cheapest itineraries by departure month (within stay range):")
    if not by_month:
        print("  (no data yet — run `tracker.py sweep` first)")
    else:
        for ym in sorted(by_month):
            top = sorted(by_month[ym], key=lambda r: r["price"])[:3]
            print(f"  {ym}:")
            for r in top:
                print(
                    f"    {r['origin']}->{r['destination']} "
                    f"dep={r['departure_date']} ret={r['return_date']} "
                    f"stay={r['stay_days']}d "
                    f"{r['price']} {r['currency']}"
                    + ("  *lowest" if r["is_lowest_price"] else "")
                )

    print()
    print(f"most recent alerts (up to {limit}):")
    alerts = recent_alerts(conn, route.name, limit=limit)
    if not alerts:
        print("  (none)")
    else:
        for a in alerts:
            print(
                f"  {a['fired_at']}  {a['origin']}->{a['destination']}  "
                f"dep={a['departure_date']} ret={a['return_date']}  "
                f"{a['price']} {a['currency']}  "
                f"(median={a['baseline_median']}, -{a['drop_pct']:.1f}%)"
            )
