"""Multi-search batch runner — what the scheduled workflow executes.

For every active search (deterministic fairness order): plan -> quote a
CostVector -> RESERVE it against the shared quota pools -> execute with
hard-stop GuardedClients -> settle reserved-vs-used -> per-search
scan_runs heartbeat. Searches that don't fit a pool or the wall-clock
budget are SKIPPED with a recorded reason — never silently degraded.
This is the plan's core promise made operational:
PREDICTED = GUARANTEED UPPER BOUND, skip-and-notify.

Usage:
    python run_batch.py [--sources ...] [--cap 25] [--trigger cron]
                        [--json-summary out.json] [--wall-budget-s 2700]

Exit codes: 0 = ran (skips are normal operation); 1 = fatal (DB/lease
bootstrap); 2 = degraded (a search errored unexpectedly).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
load_dotenv(dotenv_path=REPO / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOG = logging.getLogger("batch")

EXIT_OK, EXIT_FATAL, EXIT_DEGRADED = 0, 1, 2

# Rough per-step wall-clock estimates (seconds) for the pre-run budget
# check. Deliberately generous: overflow = skip-and-notify BEFORE
# spending anything (A8), and skipped searches lead the next run.
EST_KIWI_BAND_S = 4
EST_GF_CANDIDATE_S = 25
EST_AVIASALES_PAIR_S = 3
EST_SEARCH_OVERHEAD_S = 45


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _log_search_id(search_id: str) -> str:
    """Public CI logs must not leak user search params. Until the privacy
    flag flips (first non-owner user), ids pass through; after, only a
    stable hash prefix appears."""
    if os.environ.get("SCAN_LOG_PRIVATE", "").lower() in ("1", "true"):
        import hashlib
        return "s:" + hashlib.sha256(search_id.encode()).hexdigest()[:8]
    return search_id


# Role map for whole-role-blackout detection. R4's registry will own
# this; until then it mirrors the planner's ladders. searchapi is
# break-glass verification.
_ROLE_MAP = {
    "discovery": ("aviasales", "kiwi", "googleflights"),
    "verification": ("serpapi", "googleflights", "searchapi"),
}


def _source_roles(enabled: list[str]) -> dict[str, list[str]]:
    """Which enabled sources serve each role — the health layer pages
    when a whole role loses every live source."""
    return {role: [s for s in srcs if s in enabled]
            for role, srcs in _ROLE_MAP.items()}


def _prior_source_health(conn, this_run_id: str):
    """The source_health from the most recent PRIOR batch run, for
    transition detection. Returns a dict[str, SourceHealth] or None."""
    from lib import health as _health
    row = conn.execute(
        "SELECT summary_json FROM ledger_runs WHERE run_id != ? "
        "AND summary_json IS NOT NULL ORDER BY started_at DESC LIMIT 1",
        (this_run_id,)).fetchone()
    if row is None:
        return None
    try:
        sh = (json.loads(row["summary_json"]) or {}).get("source_health") or {}
    except (ValueError, TypeError):
        return None
    return {s: _health.SourceHealth(
                source=s, verdict=v.get("verdict", "unknown"),
                attempts=v.get("attempts", 0), ok=v.get("ok", 0),
                stored=v.get("stored", 0), detail=v.get("detail", ""),
                last_ok_at=v.get("last_ok_at"),
                effective_available=v.get("available"))
            for s, v in sh.items()}


def _estimate_seconds(plan) -> int:
    # One-way aviasales sweeps run per (pair, month); months is () for
    # round-trip, so the multiplier collapses to 1.
    av_calls = len(plan.aviasales_pairs) * (len(plan.aviasales_months) or 1)
    return (EST_SEARCH_OVERHEAD_S
            + len(plan.kiwi_bands) * EST_KIWI_BAND_S
            + len(plan.followup_candidates) * EST_GF_CANDIDATE_S
            + av_calls * EST_AVIASALES_PAIR_S)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources",
                    default="googleflights,serpapi,aviasales,kiwi")
    ap.add_argument("--cap", type=int, default=25,
                    help="max googleflights verifications per search")
    ap.add_argument("--trigger", default="local",
                    choices=["local", "cron", "dispatch", "schedule",
                             "workflow_dispatch"])
    ap.add_argument("--json-summary", type=Path, default=None)
    ap.add_argument("--wall-budget-s", type=int,
                    default=int(os.environ.get("BATCH_WALL_BUDGET_S", 2700)))
    args = ap.parse_args()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    trigger = {"schedule": "cron", "workflow_dispatch": "dispatch"}.get(
        args.trigger, args.trigger)

    from lib import db as db_mod
    from lib import health
    from lib import route_store
    from lib.clients import guard_clients, make_clients
    from lib.planner import Caps, build_run_plan, cost_vector
    from lib.quota import POOL_SEEDS, QuotaExceeded, QuotaLedger
    from lib.runner import execute_search

    started_at = _now_iso()
    t0 = datetime.now(timezone.utc)

    try:
        conn_cm = db_mod.connect(REPO / "data" / "tracker.db")
    except Exception as exc:  # noqa: BLE001
        LOG.error("FATAL: cannot connect to DB: %s", exc)
        return EXIT_FATAL

    with conn_cm as conn:
        try:
            db_mod.ensure_schema(conn)
            # C1: owner + mission search exist in the SAME deploy as the
            # runner — an empty enumeration can never silently stop scans.
            db_mod.bootstrap_owner(conn)
        except Exception as exc:  # noqa: BLE001
            LOG.error("FATAL: schema/bootstrap failed: %s", exc)
            return EXIT_FATAL

        ledger = QuotaLedger(conn)
        ledger.seed_pools()
        for seed in POOL_SEEDS:
            ledger.seed_anchor_from_snapshots(seed[0])
        orphans = ledger.expire_orphans()
        if orphans:
            LOG.warning("expired %d orphaned run lease(s)", orphans)

        # Pre-flight: construct raw clients ONCE (browser startup is the
        # expensive part; per-search we only re-wrap with that search's
        # budget). Narrow planning to the sources that actually have a
        # client — a cost line for an unavailable source (e.g. a serpapi
        # contingency with no key) would fail-closed at reserve time and
        # skip EVERY search, including the mission search.
        raw_clients, client_warnings = make_clients(sources, conn)
        for w in client_warnings:
            LOG.warning("%s", w)
        available = [s for s in sources if raw_clients.get(s) is not None]
        if available != sources:
            LOG.info("sources narrowed to available clients: %s", available)

        # Reset probe: a floored monthly pool whose anchor predates its
        # expected reset day gets ONE recorded probe call to fetch fresh
        # headers — otherwise 'never presume resets' deadlocks the pool
        # forever (headers need calls, calls need reservations, the
        # floored pool refuses reservations).
        kw = raw_clients.get("kiwi")
        if kw is not None and ledger.needs_reset_probe("kiwi"):
            from datetime import timedelta as _td
            probe_since = _now_iso()
            ev = ledger.record_spend(run_id=None, search_id=None,
                                     source="kiwi", units=1,
                                     op="reset_probe")
            probe_ok = False
            try:
                kw.one_way_search(
                    origin="MAD", destination="LHR",
                    depart_date=(datetime.now(timezone.utc)
                                 + _td(days=45)).date(),
                    currency="EUR", limit=1)
                ledger.mark(ev, "ok")
                probe_ok = True
            except Exception as exc:  # noqa: BLE001
                s = str(exc)
                ledger.mark(ev, "402" if "402" in s
                            else "429" if "429" in s else "error")
                LOG.info("kiwi reset probe: still unavailable (%s)", exc)
            # Re-anchor ONLY when the probe SUCCEEDED. A failed probe's
            # response still carries quota headers (RapidAPI decrements +
            # reports them even on a 402/429), and promoting those would
            # resurrect a dead pool — the exact bug that left a
            # payment-walled kiwi looking healthy at remaining=299 while
            # every real call 402'd (2026-07-11). A failed probe leaves
            # the existing floor in place, so bands stay skipped and the
            # probe fires again next run.
            if probe_ok:
                snap = conn.execute(
                    "SELECT remaining, limit_total FROM quota_snapshots "
                    "WHERE source='kiwi' AND remaining IS NOT NULL "
                    "  AND checked_at >= ? "
                    "ORDER BY checked_at DESC LIMIT 1",
                    (probe_since,)).fetchone()
                if snap is not None:
                    ledger.record_anchor("kiwi", remaining=snap["remaining"],
                                         limit_total=snap["limit_total"],
                                         origin="reset_probe")
                    LOG.info("kiwi re-anchored via reset probe: remaining=%s",
                             snap["remaining"])

        # serpapi's /account endpoint is FREE — anchor its pool at run
        # start so contingency reservations have a real baseline (the
        # key has often never made a metered call).
        sp = raw_clients.get("serpapi")
        if sp is not None:
            try:
                q = sp.check_quota()
                if isinstance(q.get("remaining"), int):
                    db_mod.record_quota(conn, source="serpapi",
                                        remaining=q["remaining"],
                                        limit_total=q.get("limit_total"),
                                        raw_json=json.dumps(q.get("raw", {})))
                    ledger.record_anchor("serpapi",
                                         remaining=q["remaining"],
                                         limit_total=q.get("limit_total"),
                                         origin="account_api")
            except Exception as exc:  # noqa: BLE001
                LOG.warning("serpapi account check failed: %s", exc)

        run_id = ledger.begin_run(trigger=trigger)
        if run_id is None:
            LOG.warning("another run holds the lease — exiting cleanly")
            return EXIT_OK

        rows = conn.execute(
            """
            SELECT search_id, priority, notify FROM searches
            WHERE status = 'active'
            ORDER BY CASE priority WHEN 'owner' THEN 0 ELSE 1 END,
                     COALESCE(last_scanned_at, '') ASC,
                     created_at ASC
            """
        ).fetchall()
        if not rows:
            LOG.error("FATAL: zero active searches after bootstrap — "
                      "refusing to no-op silently")
            ledger.finalize_run(run_id, "failed")
            return EXIT_FATAL
        LOG.info("batch %s: %d active search(es)", run_id, len(rows))

        caps = Caps(googleflights=args.cap, kiwi=20,
                    searchapi_sweep=0, searchapi_followup=0)
        per_search: list[dict] = []
        all_alerts: list = []
        degraded = False
        skipped = 0

        for row in rows:
            sid = row["search_id"]
            label = _log_search_id(sid)
            search_started = _now_iso()
            elapsed = (datetime.now(timezone.utc) - t0).total_seconds()

            def _skip(reason: str, plan_calls=None) -> None:
                nonlocal skipped
                skipped += 1
                LOG.info("search %s SKIPPED (%s)", label, reason)
                db_mod.insert_scan_run(
                    conn, started_at=search_started, finished_at=_now_iso(),
                    route_id=sid, trigger=trigger, sources=",".join(sources),
                    rows_stored=0, alerts_fired=0, status="skipped",
                    summary_json=json.dumps({"skip_reason": reason}),
                    batch_id=run_id,
                    reserved_json=json.dumps(plan_calls or {}))
                conn.execute(
                    "UPDATE searches SET consecutive_skips = "
                    "consecutive_skips + 1 WHERE search_id = ?", (sid,))

            try:
                route, cfg_source = route_store.load_effective_route(
                    conn, sid, REPO / "routes")
            except Exception as exc:  # noqa: BLE001
                degraded = True
                _skip(f"config unusable: {exc}")
                continue

            # Pool-aware planning: a floored/exhausted source is dropped
            # here so it never emits a cost line — one dead pool degrades
            # this search to its healthy sources instead of the
            # all-or-nothing reservation skipping the whole search
            # (2026-07-11: a floored Kiwi silently took down every search).
            # Recomputed per search so earlier searches' holds count.
            pool_states = {p.source: p for p in ledger.all_pool_states()}
            plan = build_run_plan(conn, route, sources=available, caps=caps,
                                  today=date.today(), pool_states=pool_states)
            cost = cost_vector(plan, caps=caps)

            est = _estimate_seconds(plan)
            if elapsed + est > args.wall_budget_s:
                _skip("wall_clock", cost.by_source())
                continue

            if not ledger.reserve(run_id, sid, cost,
                                  enforce_per_search_cap=(
                                      row["priority"] != "owner")):
                _skip("pool_short", cost.by_source())
                continue

            clients = guard_clients(raw_clients, ledger=ledger,
                                    run_id=run_id, search_id=sid,
                                    shadow=False)

            try:
                res = execute_search(
                    conn=conn, route=route, plan=plan, clients=clients,
                    caps=caps, alerts_log=REPO / "data" / "alerts.log",
                    serpapi_fallback_cap=cost.total("serpapi",
                                                    kind="contingency"))
            except QuotaExceeded as exc:
                # Executor tried to exceed its own reservation — a
                # planner/executor divergence BUG. The guard held the
                # promise; the run is degraded and loudly logged.
                degraded = True
                LOG.error("QUOTA GUARD TRIPPED for %s: %s — planner/executor "
                          "divergence bug", label, exc)
                ledger.settle(run_id, sid)
                continue

            ledger.settle(run_id, sid)
            reserved_vs_used = [
                dict(r) for r in conn.execute(
                    """
                    SELECT source, kind, reserved_units, used_units, state
                    FROM run_reservations
                    WHERE run_id = ? AND search_id = ?
                    """, (run_id, sid)).fetchall()
            ]
            status = "degraded" if res.degraded else "ok"
            degraded = degraded or res.degraded
            db_mod.insert_scan_run(
                conn, started_at=search_started, finished_at=_now_iso(),
                route_id=sid, trigger=trigger, sources=",".join(sources),
                rows_stored=res.rows_stored, alerts_fired=len(res.alerts),
                status=status,
                summary_json=json.dumps({"results": res.results}),
                batch_id=run_id,
                reserved_json=json.dumps(reserved_vs_used))
            conn.execute(
                """
                UPDATE searches SET last_scanned_at = ?,
                                    consecutive_skips = 0,
                                    updated_at = ?
                WHERE search_id = ?
                """, (_now_iso(), _now_iso(), sid))
            all_alerts.extend(res.alerts)
            LOG.info("search %s: rows=%d alerts=%d status=%s",
                     label, res.rows_stored, len(res.alerts), status)
            per_search.append({
                "search_id": label, "status": status,
                "rows_stored": res.rows_stored, "alerts": len(res.alerts),
                "reserved_vs_used": reserved_vs_used,
            })
            ledger.heartbeat(run_id)

        # -- close out ------------------------------------------------------
        status = "degraded" if degraded else "ok"
        try:
            ledger.capture_anchors_from_snapshots(started_at)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("anchor capture failed: %s", exc)
        conn.execute(
            "UPDATE ledger_runs SET planned_searches = ?, "
            "skipped_searches = ? WHERE run_id = ?",
            (len(rows), skipped, run_id))
        ledger.finalize_run(run_id, status)

        summary = {
            "batch_id": run_id,
            "trigger": trigger,
            "started_at": started_at,
            "finished_at": _now_iso(),
            "searches_total": len(rows),
            "searches_skipped": skipped,
            "per_search": per_search,
            # Flat combined list keeps scripts/notify_ntfy.py working
            # unchanged (it reads summary['alerts_fired']).
            "alerts_fired": [
                {"type": a.alert_type, "origin": a.origin,
                 "destination": a.destination,
                 "departure_date": a.departure_date,
                 "return_date": a.return_date,
                 "price": a.price, "currency": a.currency}
                for a in all_alerts
            ],
            "pools": {
                p.source: {"available": p.effective_available,
                           "provider_view": p.provider_view}
                for p in ledger.all_pool_states()
            },
            "status": status,
        }
        # --- source health + never-silent alerts (R1) --------------------
        # Read what the ledger already recorded and turn it into a verdict
        # per source, then page on state TRANSITIONS only (a persistent
        # outage must not re-alarm every scan). This is the layer whose
        # absence let Kiwi 402 for days unnoticed (2026-07-11).
        try:
            current_health = health.assess_sources(conn, ledger=ledger)
            summary["source_health"] = {
                s: {"verdict": h.verdict, "detail": h.detail,
                    "attempts": h.attempts, "ok": h.ok, "stored": h.stored,
                    "last_ok_at": h.last_ok_at,
                    "available": h.effective_available}
                for s, h in current_health.items()}
            summary["source_roles"] = _source_roles(available)
            summary["max_consecutive_skips"] = conn.execute(
                "SELECT COALESCE(MAX(consecutive_skips), 0) FROM searches "
                "WHERE status = 'active'").fetchone()[0]
            prior_health = _prior_source_health(conn, run_id)
            summary["health_alerts"] = health.health_pushes(
                current_health, prior_health, summary)
        except Exception as exc:  # noqa: BLE001 — health must never fail a scan
            LOG.warning("health assessment failed (non-fatal): %s", exc)
            summary["source_health"] = {}
            summary["health_alerts"] = []
        # Persist the summary so the NEXT run can diff health against it.
        try:
            conn.execute(
                "UPDATE ledger_runs SET summary_json = ? WHERE run_id = ?",
                (json.dumps(summary), run_id))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("persist summary failed (non-fatal): %s", exc)
        if args.json_summary:
            args.json_summary.write_text(json.dumps(summary, indent=2),
                                         encoding="utf-8")
        print(f"\nbatch {run_id}: {len(rows) - skipped}/{len(rows)} searches "
              f"ran, {skipped} skipped, {len(all_alerts)} alerts, {status}")

    return EXIT_DEGRADED if degraded else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
