"""Source health — reads what the ledger already records and turns it
into a per-source verdict, so a dead source can NEVER look like a quiet
week again.

Context (2026-07-11): the Kiwi proxy 402'd on every call for days and
nobody was paged, because the richest signal in the system —
`spend_events.result` (ok/empty/402/429/error/…) — was recorded and
never read, and the only alert paths were price-drops and hard process
crashes. This module reads that signal (plus the pool anchors and the
per-source stored counts in `scan_runs.summary_json`) and classifies
each source. `run_batch` folds the result into the batch summary; the
notifier turns state TRANSITIONS into pushes; `/ops` renders it.

Pure reads. No writes, no new schema.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .quota import QuotaLedger

LOG = logging.getLogger(__name__)

# Verdicts, worst first. `_SEVERITY` orders them for "did it get worse?"
# transition detection and for the /ops sort.
VERDICTS = (
    "payment_walled",  # a billing/subscription wall (402) — needs a human
    "dark",            # attempted every run, produced nothing (all failed)
    "auth_failed",     # 401/403 — bad/expired key or not subscribed
    "quota_low",       # monthly pool nearly exhausted
    "degraded",        # working but erroring or mostly-empty
    "idle",            # enabled, simply not exercised recently (not alarming)
    "live",            # recent success with data
    "unknown",         # no pool, no recent activity
)
_SEVERITY = {v: i for i, v in enumerate(VERDICTS)}
# Verdicts that warrant a push when a source ENTERS them.
ALARMING = {"payment_walled", "dark", "auth_failed", "quota_low"}

# Result labels that mean "the call did not yield data".
_FAIL_RESULTS = {"error", "429", "402", "auth_fail", "rate_limited"}


@dataclass(frozen=True)
class SourceHealth:
    source: str
    verdict: str
    attempts: int
    ok: int
    stored: int
    result_mix: dict[str, int] = field(default_factory=dict)
    last_ok_at: str | None = None
    effective_available: int | None = None
    detail: str = ""

    def worse_than(self, other: "SourceHealth | None") -> bool:
        """True when this verdict is strictly worse than `other`'s (or
        `other` is absent) — the transition that earns a push."""
        if other is None:
            return self.verdict in ALARMING
        return _SEVERITY[self.verdict] < _SEVERITY[other.verdict]


def _recent_run_ids(conn, lookback_runs: int) -> list[str]:
    rows = conn.execute(
        "SELECT run_id FROM ledger_runs ORDER BY started_at DESC LIMIT ?",
        (lookback_runs,),
    ).fetchall()
    return [r["run_id"] for r in rows]


def _stored_by_source(conn, run_ids: list[str]) -> dict[str, int]:
    """Sum per-source `stored` from scan_runs.summary_json across the
    given batch runs. summary_json shape: {"results": {src: {stored}}}."""
    out: dict[str, int] = {}
    if not run_ids:
        return out
    placeholders = ",".join("?" for _ in run_ids)
    rows = conn.execute(
        f"SELECT summary_json FROM scan_runs WHERE batch_id IN ({placeholders})",
        run_ids,
    ).fetchall()
    for row in rows:
        try:
            results = (json.loads(row["summary_json"] or "{}")
                       .get("results") or {})
        except (ValueError, TypeError):
            continue
        for src, r in results.items():
            if isinstance(r, dict) and isinstance(r.get("stored"), int):
                out[src] = out.get(src, 0) + r["stored"]
    return out


def assess_sources(conn, *, lookback_runs: int = 5,
                   ledger: QuotaLedger | None = None
                   ) -> dict[str, SourceHealth]:
    """Classify every configured/recently-active source. Pure reads."""
    ledger = ledger or QuotaLedger(conn)
    run_ids = _recent_run_ids(conn, lookback_runs)
    stored_by_source = _stored_by_source(conn, run_ids)

    # Sources to assess: every active pool, plus anything that spent
    # recently even if it has no pool (e.g. break-glass searchapi).
    sources = {r["source"] for r in conn.execute(
        "SELECT source FROM quota_pools WHERE active = 1").fetchall()}
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        sources |= {r["source"] for r in conn.execute(
            f"SELECT DISTINCT source FROM spend_events "
            f"WHERE run_id IN ({placeholders})", run_ids).fetchall()}

    caps = {r["source"]: r["per_search_cap"] for r in conn.execute(
        "SELECT source, per_search_cap FROM quota_pools").fetchall()}

    out: dict[str, SourceHealth] = {}
    for source in sorted(sources):
        # Result mix over the recent runs (the never-before-read signal).
        mix: dict[str, int] = {}
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            for r in conn.execute(
                f"SELECT result, COUNT(*) AS n FROM spend_events "
                f"WHERE source = ? AND run_id IN ({placeholders}) "
                f"GROUP BY result", [source, *run_ids]).fetchall():
                mix[r["result"]] = r["n"]
        attempts = sum(mix.values())
        ok = mix.get("ok", 0)
        empty = mix.get("empty", 0)
        fails = sum(n for res, n in mix.items() if res in _FAIL_RESULTS)
        stored = stored_by_source.get(source, 0)

        last_ok = conn.execute(
            "SELECT MAX(spent_at) FROM spend_events "
            "WHERE source = ? AND result = 'ok'", (source,)).fetchone()[0]

        state = ledger.pool_state(source)
        avail = state.effective_available if state else None
        origin = state.baseline_origin if state else None
        per_search_cap = caps.get(source)

        verdict, detail = _classify(
            origin=origin, mix=mix, attempts=attempts, ok=ok, empty=empty,
            fails=fails, stored=stored, avail=avail,
            per_search_cap=per_search_cap)

        out[source] = SourceHealth(
            source=source, verdict=verdict, attempts=attempts, ok=ok,
            stored=stored, result_mix=mix, last_ok_at=last_ok,
            effective_available=avail, detail=detail)
    return out


def _classify(*, origin, mix, attempts, ok, empty, fails, stored, avail,
              per_search_cap) -> tuple[str, str]:
    # 1. A payment/subscription wall is the most urgent — a human must
    #    fix billing. Signalled by the 402 floor OR a raw 402 this window.
    if origin == "quota_402_floor" or mix.get("402"):
        return "payment_walled", "402 Payment Required — check the subscription/billing"
    # 2. Auth failure — bad/expired key or not subscribed.
    if mix.get("auth_fail"):
        return "auth_failed", "401/403 — API key rejected or not subscribed"
    # 3. Dark: attempted this window but produced NOTHING and every call
    #    failed. This is the exact 2026-07-11 shape (Kiwi all-errors, 0 rows).
    if attempts > 0 and ok == 0 and stored == 0 and fails == attempts:
        return "dark", f"{fails}/{attempts} calls failed, 0 rows stored"
    # 4. Monthly pool nearly exhausted (still alive, warn early).
    if avail is not None and per_search_cap:
        if avail <= 0:
            return "quota_low", "pool exhausted (0 available)"
        if avail < 2 * per_search_cap:
            return "quota_low", f"{avail} available (< 2x per-search cap)"
    # 5. Working but noisy — some failures, or mostly-empty responses.
    if ok > 0 and (fails > 0 or (empty and empty >= ok)):
        return "degraded", f"ok={ok} empty={empty} fail={fails}"
    # 6. Live — a recent success with data.
    if ok > 0:
        return "live", f"ok={ok}"
    # 7. Enabled but not exercised recently.
    if attempts == 0:
        return "idle", "not called in the recent window"
    return "unknown", ""


def health_pushes(current: dict[str, SourceHealth],
                  prior: dict[str, SourceHealth] | None,
                  summary: dict) -> list[dict]:
    """Decide which pushes to send THIS run — transition-based so a
    persistent outage doesn't re-page every scan (that trains
    notification blindness). Returns ntfy-ready dicts.

    Rules:
      - a source ENTERS an alarming verdict (worse than last run)
      - a whole ROLE loses all live sources (SEV1) — role map passed in summary
      - a ran (non-skipped) search stored 0 rows across the batch
      - consecutive_skips >= 3 on any active search
    """
    prior = prior or {}
    pushes: list[dict] = []

    for src, h in current.items():
        if h.verdict in ALARMING and h.worse_than(prior.get(src)):
            pri = "high" if h.verdict in ("payment_walled", "dark") else "default"
            pushes.append({
                "title": f"Source down: {src} ({h.verdict.replace('_', ' ')})",
                "body": f"{src}: {h.detail}. "
                        f"Last OK: {h.last_ok_at or 'never'}.",
                "priority": pri, "tags": "warning,satellite_antenna"})

    # Whole-role blackout: if a role's every source is non-live/idle.
    roles = summary.get("source_roles") or {}
    for role, srcs in roles.items():
        have_live = any(current.get(s) and current[s].verdict in ("live", "degraded")
                        for s in srcs)
        had_live = any(prior.get(s) and prior[s].verdict in ("live", "degraded")
                       for s in srcs)
        if srcs and not have_live and had_live:
            pushes.append({
                "title": f"No working {role} source",
                "body": f"Every {role} source is down ({', '.join(srcs)}). "
                        f"Scans can't {role} until one recovers.",
                "priority": "high", "tags": "rotating_light"})

    # A search that ran but stored nothing.
    zero = [p["search_id"] for p in summary.get("per_search", [])
            if p.get("status") == "ok" and p.get("rows_stored") == 0]
    if zero:
        pushes.append({
            "title": "Scan stored 0 rows",
            "body": f"Ran but stored nothing: {', '.join(zero)}. "
                    f"Discovery may be dark.",
            "priority": "high", "tags": "warning"})

    # Chronically skipped searches.
    max_skips = summary.get("max_consecutive_skips") or 0
    if max_skips >= 3:
        pushes.append({
            "title": f"Search skipped {max_skips} runs in a row",
            "body": "A search keeps getting skipped (pool short). "
                    "Check source health on /ops.",
            "priority": "default", "tags": "warning"})

    return pushes
