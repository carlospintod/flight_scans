"""Terminal report.

Sections:
  1. Sky Scrapper departure-curve highlights per (origin, destination)
  2. Cheapest SearchAPI round-trip itineraries by departure month
  3. Side-by-side carrier detail when both sources have point-queried
     the same itinerary
  4. Recent alerts
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from .config import RouteConfig
from .db import cheapest_recent_itineraries, latest_curve, recent_alerts
from .searchapi_io import SOURCE_ID as SEARCHAPI_SOURCE
from .skyscanner_rapidapi import SOURCE_ID as SKYSCANNER_SOURCE


def print_report(*, conn: sqlite3.Connection, route: RouteConfig, limit: int = 20) -> None:
    print(f"== {route.name} ==")

    _print_skyscanner_curve(conn, route)
    _print_searchapi_grid(conn, route, limit)
    _print_point_query_compare(conn, route)
    _print_recent_alerts(conn, route, limit)


def _print_skyscanner_curve(conn: sqlite3.Connection, route: RouteConfig) -> None:
    print()
    print("== Sky Scrapper departure-curve (cheapest 8 days per origin) ==")
    any_data = False
    for origin in route.origins:
        for destination in route.destinations:
            rows = latest_curve(
                conn, route.name,
                origin=origin, destination=destination,
                source=SKYSCANNER_SOURCE,
            )
            if not rows:
                continue
            any_data = True
            top = sorted(rows, key=lambda r: r["price"])[:8]
            print(f"  {origin}->{destination}  (snapshot {rows[0]['snapshot_at']})")
            for r in top:
                grp = f" [{r['price_group']}]" if r["price_group"] else ""
                print(
                    f"    dep={r['departure_date']}  "
                    f"{r['price']:>7.2f} {r['currency']}{grp}"
                )
    if not any_data:
        print("  (no data yet — run `tracker.py sweep` first)")


def _print_searchapi_grid(
    conn: sqlite3.Connection, route: RouteConfig, limit: int,
) -> None:
    print()
    print("== SearchAPI cheapest round-trips by departure month (within stay range) ==")
    rows = cheapest_recent_itineraries(
        conn,
        route.name,
        min_stay=route.stay.min_days,
        max_stay=route.stay.max_days,
        limit=limit * 6,
        source=SEARCHAPI_SOURCE,
    )
    by_month: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_month[r["departure_date"][:7]].append(r)
    if not by_month:
        print("  (no data yet)")
        return
    for ym in sorted(by_month):
        top = sorted(by_month[ym], key=lambda r: r["price"])[:3]
        print(f"  {ym}:")
        for r in top:
            print(
                f"    {r['origin']}->{r['destination']}  "
                f"dep={r['departure_date']} ret={r['return_date']}  "
                f"stay={r['stay_days']}d  "
                f"{r['price']} {r['currency']}"
                + ("  *lowest" if r["is_lowest_price"] else "")
            )


def _print_point_query_compare(conn: sqlite3.Connection, route: RouteConfig) -> None:
    """For itineraries that both sources have point-queried recently,
    show the best (rank=0) flight from each side by side.
    """
    rows = list(conn.execute(
        """
        SELECT pq.* FROM point_queries pq
        JOIN (
            SELECT source, origin, destination, departure_date, return_date,
                   MAX(snapshot_at) AS latest
            FROM point_queries
            WHERE route_id = ? AND rank = 0
            GROUP BY source, origin, destination, departure_date, return_date
        ) m ON m.source = pq.source AND m.origin = pq.origin
           AND m.destination = pq.destination
           AND m.departure_date = pq.departure_date
           AND m.return_date = pq.return_date
           AND m.latest = pq.snapshot_at
        WHERE pq.route_id = ? AND pq.rank = 0
        ORDER BY pq.departure_date ASC, pq.return_date ASC, pq.source ASC
        """,
        (route.name, route.name),
    ))
    grouped: dict[tuple, dict[str, sqlite3.Row]] = defaultdict(dict)
    for r in rows:
        key = (r["origin"], r["destination"], r["departure_date"], r["return_date"])
        grouped[key][r["source"]] = r

    multi_source = [(k, v) for k, v in grouped.items() if len(v) >= 1]
    if not multi_source:
        return
    print()
    print("== Point-query detail (cheapest best_flight per source) ==")
    for (o, d, dep, ret), srcs in multi_source:
        print(f"  {o}->{d}  dep={dep} ret={ret}")
        for src in (SEARCHAPI_SOURCE, SKYSCANNER_SOURCE):
            if src not in srcs:
                continue
            r = srcs[src]
            tag = ""
            if "is_self_transfer" in r.keys() and r["is_self_transfer"]:
                tag = "  [self-transfer]"
            dur = ""
            if r["total_minutes"]:
                h, m = divmod(int(r["total_minutes"]), 60)
                dur = f"  {h}h{m:02d}m"
            print(
                f"    [{src:>11}]  {r['price']} {r['currency']}  "
                f"{r['carriers']}  stops={r['stops']}{dur}{tag}"
            )


def _print_recent_alerts(
    conn: sqlite3.Connection, route: RouteConfig, limit: int,
) -> None:
    print()
    print(f"== Recent alerts (up to {limit}) ==")
    alerts = recent_alerts(conn, route.name, limit=limit)
    if not alerts:
        print("  (none)")
        return
    for a in alerts:
        src = a["source"] if "source" in a.keys() else "?"
        print(
            f"  {a['fired_at']}  [{src}]  {a['origin']}->{a['destination']}  "
            f"dep={a['departure_date']} ret={a['return_date']}  "
            f"{a['price']} {a['currency']}  "
            f"(median={a['baseline_median']}, -{a['drop_pct']:.1f}%)"
        )
