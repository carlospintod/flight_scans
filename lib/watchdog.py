"""Dead-man watchdog for both projects (plan 2026-11-14, A5).

Every other alarm in this repo fires from INSIDE a run: the scan's
source-health pushes, deals.yml's failure push. None of them can fire
when the run never happens — a disabled schedule, a workflow GitHub
stopped starting, a secret that kills the job before the summary. This
module reads only what the runs leave behind in the database and says
which of them has gone quiet. No metered calls, no provider calls.

Checks (each one named, so state and pushes are per check):

  vuelazo_collector  no Explore observation stored in the last 48 h
  nbo_scan           no successful Nairobi scan in the last 4 days
  nbo_calendar       a calendar strip whose last two sweeps both errored
  pool:<source>      a SerpApi pool runs out before its renewal date at
                     the last 7 days' burn rate

Not checked here because the scan already pushes it on transition
(lib/health.py): a scan that stored 0 rows, a source gone dark or
payment-walled.

Pushes follow transitions: into a bad state -> push; still bad -> push
again only every REPEAT_DAYS; back to ok -> one low-priority note.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

COLLECTOR_STALE_H = 48
SCAN_STALE_DAYS = 4
CALENDAR_LOOKBACK_DAYS = 30
# Two sweep cycles. A calendar sweep is a lump (62 calls on 2026-11-14)
# against ~6 per regular scan; inside a 7-day window that one lump read
# as 12.6/day and "runs out 24 Nov", while the 28-day rate (5.3/day)
# showed the truth: 116 needed of 129 usable — tight, not exhausted.
# A runaway loop is bounded anyway: the ledger refuses past the margin.
BURN_WINDOW_DAYS = 28
REPEAT_DAYS = 3
POOLS = ("serpapi", "serpapi_vz")
# The pool a project depends on, for the push wording.
POOL_OWNER = {"serpapi": "Nairobi tracker", "serpapi_vz": "Vuelazo collector"}

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchdog_state (
    check_id        TEXT PRIMARY KEY,
    status          TEXT NOT NULL,     -- 'ok' | 'bad'
    since           TEXT NOT NULL,     -- when it entered this status
    last_pushed_at  TEXT,
    detail          TEXT
);
"""


@dataclass(frozen=True)
class Finding:
    check_id: str
    bad: bool
    title: str          # push title when bad
    detail: str         # one line, always filled (also for ok)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age(now: datetime, then: datetime) -> str:
    hours = (now - then).total_seconds() / 3600
    return f"{hours:.0f} h" if hours < 72 else f"{hours / 24:.1f} days"


def check_collector(conn, now: datetime) -> Finding:
    row = conn.execute(
        "SELECT MAX(observed_at) AS last, COUNT(*) AS n FROM fare_observations "
        "WHERE source = 'explore' AND observed_at >= ?",
        (_iso(now - timedelta(days=7)),)).fetchone()
    last = _parse(row["last"]) if row else None
    if last is None:
        ever = conn.execute(
            "SELECT MAX(observed_at) FROM fare_observations "
            "WHERE source = 'explore'").fetchone()[0]
        last = _parse(ever)
    if last is None:
        return Finding("vuelazo_collector", True,
                       "Vuelazo: el colector nunca ha guardado precios",
                       "No Explore observation has ever been stored. "
                       "Check the deals workflow and SERPAPI_KEY_VZ.")
    if now - last > timedelta(hours=COLLECTOR_STALE_H):
        return Finding("vuelazo_collector", True,
                       "Vuelazo: el colector lleva parado "
                       + _age(now, last),
                       f"Last Explore row stored {_iso(last)} "
                       f"({_age(now, last)} ago; limit {COLLECTOR_STALE_H} h). "
                       f"Check the deals workflow runs in Actions.")
    return Finding("vuelazo_collector", False, "",
                   f"last Explore row {_iso(last)} ({_age(now, last)} ago), "
                   f"{row['n'] if row else 0} rows in 7 days")


def check_scan(conn, now: datetime) -> Finding:
    last_ok = _parse(conn.execute(
        "SELECT MAX(started_at) FROM scan_runs WHERE status = 'ok'"
    ).fetchone()[0])
    last_any = conn.execute(
        "SELECT started_at, status FROM scan_runs "
        "ORDER BY started_at DESC LIMIT 1").fetchone()
    tail = (f" Latest run: {last_any['started_at']} ({last_any['status']})."
            if last_any else " No scan run is recorded at all.")
    if last_ok is None:
        return Finding("nbo_scan", True,
                       "Nairobi: ningún escaneo correcto registrado",
                       "No successful scan is recorded." + tail)
    if now - last_ok > timedelta(days=SCAN_STALE_DAYS):
        return Finding("nbo_scan", True,
                       "Nairobi: sin escaneo correcto desde hace "
                       + _age(now, last_ok),
                       f"Last successful scan {_iso(last_ok)} "
                       f"({_age(now, last_ok)} ago; limit {SCAN_STALE_DAYS} "
                       f"days).{tail} Check scan.yml in Actions.")
    return Finding("nbo_scan", False, "",
                   f"last successful scan {_iso(last_ok)} "
                   f"({_age(now, last_ok)} ago)")


def check_calendar(conn, now: datetime) -> Finding:
    """A strip (route, origin, stay) whose two most recent sweeps inside
    the lookback both errored. Strips that left the window stop being
    swept and age out of the lookback on their own."""
    rows = conn.execute(
        "SELECT route_id, origin, destination, stay_days, status, swept_at "
        "FROM calendar_sweeps WHERE swept_at >= ? "
        "ORDER BY swept_at DESC, sweep_id DESC",
        (_iso(now - timedelta(days=CALENDAR_LOOKBACK_DAYS)),)).fetchall()
    last_two: dict[tuple, list[str]] = {}
    for r in rows:
        key = (r["route_id"], r["origin"], r["destination"], r["stay_days"])
        seen = last_two.setdefault(key, [])
        if len(seen) < 2:
            seen.append(r["status"])
    failing = sorted(k for k, s in last_two.items()
                     if len(s) == 2 and all(x == "error" for x in s))
    if failing:
        names = ", ".join(f"{o}→{d} {stay}d" for _, o, d, stay in failing[:6])
        more = f" (+{len(failing) - 6} more)" if len(failing) > 6 else ""
        return Finding("nbo_calendar", True,
                       f"Nairobi: {len(failing)} tira(s) de calendario "
                       f"fallan dos veces seguidas",
                       f"Last two sweeps errored for {len(failing)} of "
                       f"{len(last_two)} strips: {names}{more}.")
    return Finding("nbo_calendar", False, "",
                   f"{len(last_two)} strips swept in {CALENDAR_LOOKBACK_DAYS} "
                   f"days, none failing twice")


def _renewal_date(conn, source: str) -> date | None:
    row = conn.execute(
        "SELECT raw_json FROM quota_snapshots WHERE source = ? "
        "ORDER BY checked_at DESC LIMIT 1", (source,)).fetchone()
    if not row or not row[0]:
        return None
    try:
        raw = json.loads(row[0])
        return date.fromisoformat(str(raw.get("plan_renewal_date"))[:10])
    except (ValueError, TypeError, AttributeError):
        return None


def check_pool(conn, now: datetime, source: str) -> Finding:
    from .quota import QuotaLedger

    cid = f"pool:{source}"
    state = QuotaLedger(conn).pool_state(source)
    if state is None or state.provider_view is None:
        return Finding(cid, False, "", "no anchor yet — not judged")
    spent = conn.execute(
        "SELECT COALESCE(SUM(units), 0) FROM spend_events "
        "WHERE source = ? AND spent_at >= ?",
        (source, _iso(now - timedelta(days=BURN_WINDOW_DAYS)))).fetchone()[0]
    per_day = spent / BURN_WINDOW_DAYS
    renew = _renewal_date(conn, source)
    if renew is None:
        return Finding(cid, False, "",
                       f"{state.effective_available} usable, "
                       f"{per_day:.1f}/day; renewal date unknown — not judged")
    # A renewal date in the past means the snapshot predates the reset:
    # the next probe re-anchors, so there is nothing to judge yet.
    days_left = (renew - now.date()).days
    if days_left <= 0:
        return Finding(cid, False, "",
                       f"renewal {renew} passed; waiting for a fresh anchor")
    need = per_day * days_left
    line = (f"{state.effective_available} usable (after margin "
            f"{state.safety_margin}), burning {per_day:.1f}/day over "
            f"{BURN_WINDOW_DAYS} days, {days_left} days to renewal {renew}: "
            f"needs ~{need:.0f}")
    if need > state.effective_available:
        runs_out = (now.date() + timedelta(
            days=int(state.effective_available / per_day))) if per_day else renew
        return Finding(cid, True,
                       f"{POOL_OWNER.get(source, source)}: el pool {source} "
                       f"se agota antes del {renew}",
                       line + f". At this rate it runs out around {runs_out}.")
    return Finding(cid, False, "", line)


def evaluate(conn, now: datetime) -> list[Finding]:
    return [check_collector(conn, now), check_scan(conn, now),
            check_calendar(conn, now),
            *(check_pool(conn, now, s) for s in POOLS)]


@dataclass(frozen=True)
class Push:
    title: str
    body: str
    priority: str
    tags: str


def plan_pushes(conn, findings: list[Finding], now: datetime
                ) -> tuple[list[Push], list[tuple]]:
    """Decide the pushes from the stored state. Returns (pushes,
    state_rows) — the caller writes state_rows only when it is not a dry
    run, so a demonstration never moves the state machine."""
    try:
        prev = {r["check_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM watchdog_state").fetchall()}
    except Exception:  # noqa: BLE001 — first run: no table yet, no state
        prev = {}
    pushes: list[Push] = []
    rows: list[tuple] = []
    stamp = _iso(now)
    for f in findings:
        p = prev.get(f.check_id)
        was_bad = bool(p and p["status"] == "bad")
        if f.bad:
            since = p["since"] if was_bad else stamp
            last = _parse(p["last_pushed_at"]) if was_bad and p else None
            due = (not was_bad or last is None
                   or now - last >= timedelta(days=REPEAT_DAYS))
            if due:
                again = (f" (still bad since {since[:10]})" if was_bad else "")
                pushes.append(Push(f.title, f.detail + again,
                                   "high", "rotating_light"))
            rows.append((f.check_id, "bad", since,
                         stamp if due else p["last_pushed_at"], f.detail))
        else:
            if was_bad:
                pushes.append(Push(f"Watchdog: {f.check_id} OK otra vez",
                                   f"Recovered: {f.detail}", "low",
                                   "white_check_mark"))
            since = p["since"] if (p and not was_bad) else stamp
            rows.append((f.check_id, "ok", since,
                         p["last_pushed_at"] if p else None, f.detail))
    return pushes, rows


def save_state(conn, rows: list[tuple]) -> None:
    conn.executescript(STATE_SCHEMA)
    for r in rows:
        conn.execute(
            "INSERT INTO watchdog_state (check_id, status, since, "
            "last_pushed_at, detail) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(check_id) DO UPDATE SET status = excluded.status, "
            "since = excluded.since, last_pushed_at = excluded.last_pushed_at, "
            "detail = excluded.detail", r)
