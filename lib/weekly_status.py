"""Weekly Monday status push (plan 2026-11-14, A6; wording approved by
Carlos on 2026-11-14 as drafted in
docs/notes/passive-vuelazo-reliable-nbo-2026-11-14.md).

One short message every Monday, sent even in a quiet week: silence itself
is then the signal that something stopped. It covers the previous full
week, Monday 00:00 to Monday 00:00 UTC:

    Weekly status: 9–15 Nov
    Vuelazo collector: 7 of 7 days ran, 790 fares stored.
    Baselines: 31 of 186 routes have 4+ observations.
    Nairobi: 3 of 3 scans ok; calendar sweep 20 of 20 strips.
    Best verified: 549 € MAD 3 Feb → 14 Apr (−8 € vs last week).
    Cheapest by stay: ~1 month 480 € (30 d) · ~2 months 549 € (70 d) · ~3 months 612 € (86 d).
    Window: departures 27 Dec – 9 Mar (as of the last scan).

Definitions (the questions that went with the draft, answered "as drafted"):
  * a collector day = a UTC day with at least one Explore row stored;
  * a route = an (origin, destination) pair Explore has ever priced; it has
    a baseline at BASELINE_MIN_OBS (4) or more Explore observations;
  * scans = the owner search's scan_runs rows in the week, skipped ones
    left out; "ok" = status ok;
  * strips = the window's planned strips (lib/sweep.plan_strips) with an
    ok sweep in the week;
  * best verified = cheapest googleflights/serpapi fare STORED in the week
    for a trip inside the current window and stay range; the change is
    against the same figure for the week before;
  * cheapest by stay = the cheapest Google-family price stored in the week
    (calendar cells included: they are leads, which is why this line is
    separate from "best verified"), in STAY_BUCKETS.

Read-only: no metered calls, no writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .alerts import WINDOW_BEST_SOURCES, _trip_in_window
from .serpapi_io import CALENDAR_SOURCE_ID

BASELINE_MIN_OBS = 4
STAY_BUCKETS = (("~1 month", 30, 44), ("~2 months", 45, 74),
                ("~3 months", 75, 90))
# Google-family sources for "cheapest by stay": the verified ones plus the
# SerpApi calendar cells (Google prices, not yet re-checked).
CHEAPEST_SOURCES = tuple(sorted(WINDOW_BEST_SOURCES | {CALENDAR_SOURCE_ID}))
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class WeeklyStatus:
    title: str
    body: str


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _d(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]}"


def week_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The last full Monday-to-Monday week (UTC) before `now`."""
    now = now.astimezone(timezone.utc)
    this_monday = datetime(now.year, now.month, now.day,
                           tzinfo=timezone.utc) - timedelta(days=now.weekday())
    return this_monday - timedelta(days=7), this_monday


def period_label(start: datetime, end: datetime) -> str:
    """'9–15 Nov', or '30 Nov – 6 Dec' across a month."""
    a, b = start.date(), (end - timedelta(days=1)).date()
    if a.month == b.month:
        return f"{a.day}–{b.day} {_MONTHS[b.month - 1]}"
    return f"{_d(a)} – {_d(b)}"


def _eur(price: int) -> str:
    return f"{price} €"


def collector_line(conn, start: datetime, end: datetime) -> str:
    row = conn.execute(
        "SELECT COUNT(DISTINCT substr(observed_at, 1, 10)) AS days, "
        "COUNT(*) AS n FROM fare_observations WHERE source = 'explore' "
        "AND observed_at >= ? AND observed_at < ?",
        (_iso(start), _iso(end))).fetchone()
    days, n = int(row["days"] or 0), int(row["n"] or 0)
    total_days = (end - start).days
    return (f"Vuelazo collector: {days} of {total_days} days ran, "
            f"{n:,} fare{'' if n == 1 else 's'} stored.")


def baselines_line(conn, end: datetime) -> str:
    row = conn.execute(
        "SELECT COUNT(*) AS routes, "
        "SUM(CASE WHEN n >= ? THEN 1 ELSE 0 END) AS based FROM ("
        "  SELECT origin, dest, COUNT(*) AS n FROM fare_observations "
        "  WHERE source = 'explore' AND observed_at < ? "
        "  GROUP BY origin, dest)",
        (BASELINE_MIN_OBS, _iso(end))).fetchone()
    return (f"Baselines: {int(row['based'] or 0):,} of "
            f"{int(row['routes'] or 0):,} routes have "
            f"{BASELINE_MIN_OBS}+ observations.")


def nairobi_line(conn, route, start: datetime, end: datetime) -> str:
    from .sweep import plan_strips

    runs = conn.execute(
        "SELECT status FROM scan_runs WHERE route_id = ? AND status != "
        "'skipped' AND started_at >= ? AND started_at < ?",
        (route.name, _iso(start), _iso(end))).fetchall()
    ok = sum(1 for r in runs if r["status"] == "ok")
    planned = {(s.origin, s.stay_days)
               for s in plan_strips(route, today=end.date())}
    swept = {(r["origin"], r["stay_days"]) for r in conn.execute(
        "SELECT origin, stay_days FROM calendar_sweeps WHERE route_id = ? "
        "AND status = 'ok' AND swept_at >= ? AND swept_at < ?",
        (route.name, _iso(start), _iso(end))).fetchall()}
    return (f"Nairobi: {ok} of {len(runs)} scans ok; calendar sweep "
            f"{len(planned & swept)} of {len(planned)} strips.")


def _in_window_rows(conn, route, sources, start: datetime, end: datetime):
    marks = ",".join("?" * len(sources))
    rows = conn.execute(
        f"SELECT origin, departure_date, return_date, stay_days, price "
        f"FROM calendar_snapshots WHERE route_id = ? AND source IN ({marks}) "
        f"AND snapshot_at >= ? AND snapshot_at < ? AND price > 0",
        (route.name, *sources, _iso(start), _iso(end))).fetchall()
    return [r for r in rows if _trip_in_window(
        route, r["departure_date"], r["return_date"], r["stay_days"])]


def best_verified_line(conn, route, start: datetime, end: datetime) -> str:
    sources = sorted(WINDOW_BEST_SOURCES)
    week = _in_window_rows(conn, route, sources, start, end)
    if not week:
        return "Best verified: no verified fare in the window this week."
    best = min(week, key=lambda r: (r["price"], r["departure_date"]))
    line = (f"Best verified: {_eur(best['price'])} {best['origin']} "
            f"{_d(date.fromisoformat(best['departure_date']))} → "
            f"{_d(date.fromisoformat(best['return_date']))}")
    prev = _in_window_rows(conn, route, sources, start - timedelta(days=7),
                           start)
    if prev:
        delta = best["price"] - min(r["price"] for r in prev)
        sign = "−" if delta < 0 else "+" if delta > 0 else "±"
        line += f" ({sign}{abs(delta)} € vs last week)"
    return line + "."


def cheapest_by_stay_line(conn, route, start: datetime, end: datetime) -> str:
    rows = _in_window_rows(conn, route, CHEAPEST_SOURCES, start, end)
    parts = []
    for label, lo, hi in STAY_BUCKETS:
        bucket = [r for r in rows if lo <= (r["stay_days"] or 0) <= hi]
        if bucket:
            b = min(bucket, key=lambda r: (r["price"], r["stay_days"]))
            parts.append(f"{label} {_eur(b['price'])} ({b['stay_days']} d)")
        else:
            parts.append(f"{label} —")
    return "Cheapest by stay: " + " · ".join(parts) + "."


def window_line(route) -> str:
    sw = route.search_window
    last = sw.last_departure(0 if route.is_one_way else route.stay.min_days)
    return (f"Window: departures {_d(sw.earliest_departure)} – {_d(last)} "
            f"(as of the last scan).")


def build(conn, route, now: datetime) -> WeeklyStatus:
    """The push for the week before `now`. `route` is the owner Nairobi
    search as resolved today, or None when there is no active owner search
    (the Nairobi lines then say so instead of disappearing)."""
    start, end = week_bounds(now)
    lines = [collector_line(conn, start, end), baselines_line(conn, end)]
    if route is None:
        lines.append("Nairobi: no active owner search.")
    else:
        lines += [nairobi_line(conn, route, start, end),
                  best_verified_line(conn, route, start, end),
                  cheapest_by_stay_line(conn, route, start, end),
                  window_line(route)]
    return WeeklyStatus(title=f"Weekly status: {period_label(start, end)}",
                        body="\n".join(lines))
