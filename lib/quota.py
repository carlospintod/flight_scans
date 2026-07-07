"""Quota ledger: pools, spend recording, and the client guard.

Product invariant (the plan's hard promise): PREDICTED = GUARANTEED
UPPER BOUND per source. The mechanics that make it true live here:

- charge-before-call, no refunds — one spend_events row per HTTP
  attempt (providers meter failed calls too);
- ledger-primary, provider-re-anchored — our own spend counts
  instantly; provider headers/account APIs only move the baseline
  (pool_anchors), ordered by spend event_id, not wall-clock;
- resets are never presumed — a pool is credited only when a provider
  observation proves replenishment.

M1 ships this in SHADOW mode: everything records, nothing refuses.
M2 adds reservation enforcement (run_reservations CAS) on top of the
same tables. GuardedClient(shadow=False) already implements the
hard-stop for M2 — refuse at 0 with QuotaExceeded.

Turso constraint (lib/turso_http.py): autocommit per statement — every
correctness mutation here is a single SQL statement.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

LOG = logging.getLogger(__name__)

# Sources deliberately absent (product plan cuts E1-E3): priceline,
# skyscanner, searchapi (local break-glass, unmodeled), gflights2
# (deferred to the paid milestone). Adding one later = one INSERT here
# plus a manual bootstrap anchor from /ops.
POOL_SEEDS: tuple[tuple, ...] = (
    # (source, pool_kind, period_limit, reset_anchor_day, safety_margin,
    #  per_search_cap, per_run_cap)
    ("kiwi", "monthly", 300, 10, 15, 10, None),
    ("serpapi", "monthly", 250, None, 25, 7, None),
    ("aviasales", "rate_only", None, None, 0, None, None),
    ("googleflights", "per_run", None, None, 0, 25, 30),
)

# Metered method -> worst-case units per invocation. The guard charges
# these BEFORE the call. Methods not listed pass through unmetered
# (source_id, check_quota, context-manager protocol...).
METERED: dict[str, dict[str, int]] = {
    "kiwi": {"range_search": 1, "round_trip_search": 1, "one_way_search": 1},
    "serpapi": {"point_query": 1},
    "searchapi": {"point_query": 1, "calendar": 1},
    "googleflights": {"point_query": 1},
    "aviasales": {"cheap_prices": 1},
    "skyscanner": {"point_query": 2, "search_airport": 1},
}


class QuotaExceeded(RuntimeError):
    """spend() at 0 remaining. Catching one means a planner/executor
    divergence bug — the run must be marked degraded, never absorbed."""


@dataclass(frozen=True)
class PoolState:
    source: str
    pool_kind: str
    period_limit: int | None
    provider_view: int | None      # anchor - spend since anchor
    holds: int                     # other live runs' held reservations
    safety_margin: int
    effective_available: int | None  # provider_view - margin - holds; None = unmetered
    baseline_at: str | None
    baseline_origin: str | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class QuotaLedger:
    """All reads/writes on the ledger tables. One instance per process."""

    def __init__(self, conn):
        self._conn = conn

    # -- seeding / bootstrap -------------------------------------------------

    def seed_pools(self) -> None:
        """Idempotent: INSERT OR IGNORE only — /ops edits (e.g. the $5
        Kiwi Pro switch: period_limit 300 -> 20000) survive re-seeding."""
        now = _now_iso()
        for (source, kind, limit, reset_day, margin,
             per_search, per_run) in POOL_SEEDS:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO quota_pools
                    (source, pool_kind, period_limit, reset_anchor_day,
                     safety_margin, per_search_cap, per_run_cap, active,
                     updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (source, kind, limit, reset_day, margin,
                 per_search, per_run, now),
            )

    def seed_anchor_from_snapshots(self, source: str) -> bool:
        """Bootstrap a pool's baseline from the latest quota_snapshots
        row (the clients have been capturing provider headers all along).
        No-op when an anchor already exists or no snapshot is available.
        Returns True when an anchor was created."""
        existing = self._conn.execute(
            "SELECT 1 FROM pool_anchors WHERE source = ? LIMIT 1", (source,)
        ).fetchone()
        if existing:
            return False
        snap = self._conn.execute(
            """
            SELECT checked_at, remaining, limit_total FROM quota_snapshots
            WHERE source = ? AND remaining IS NOT NULL
            ORDER BY checked_at DESC LIMIT 1
            """,
            (source,),
        ).fetchone()
        if not snap:
            return False
        self.record_anchor(source, remaining=snap["remaining"],
                           limit_total=snap["limit_total"], origin="seed")
        LOG.info("quota: seeded %s anchor from snapshot %s (remaining=%s)",
                 source, snap["checked_at"], snap["remaining"])
        return True

    # -- anchors ---------------------------------------------------------------

    def record_anchor(self, source: str, *, remaining: int,
                      limit_total: int | None, origin: str) -> None:
        """A provider observation re-anchors the pool. Spend recorded
        AFTER the current max event_id counts against this anchor —
        event ordering, not wall-clock (same-second collisions)."""
        self._conn.execute(
            """
            INSERT INTO pool_anchors
                (source, baseline_remaining, limit_total,
                 last_spend_event_id, origin, baseline_at)
            VALUES (?, ?, ?,
                    COALESCE((SELECT MAX(event_id) FROM spend_events), 0),
                    ?, ?)
            """,
            (source, remaining, limit_total, origin, _now_iso()),
        )

    def capture_anchors_from_snapshots(self, since_iso: str) -> int:
        """After a run: promote fresh provider observations (written by
        the clients into quota_snapshots during the run) into anchors.
        Only strictly newer observations become anchors."""
        n = 0
        for source in [s[0] for s in POOL_SEEDS]:
            snap = self._conn.execute(
                """
                SELECT checked_at, remaining, limit_total FROM quota_snapshots
                WHERE source = ? AND remaining IS NOT NULL AND checked_at >= ?
                ORDER BY checked_at DESC LIMIT 1
                """,
                (source, since_iso),
            ).fetchone()
            if snap is None:
                continue
            self.record_anchor(source, remaining=snap["remaining"],
                               limit_total=snap["limit_total"],
                               origin="header")
            n += 1
        return n

    # -- state -----------------------------------------------------------------

    def pool_state(self, source: str, *, exclude_run: str | None = None
                   ) -> PoolState | None:
        pool = self._conn.execute(
            "SELECT * FROM quota_pools WHERE source = ? AND active = 1",
            (source,),
        ).fetchone()
        if pool is None:
            return None
        if pool["pool_kind"] in ("rate_only", "per_run"):
            return PoolState(
                source=source, pool_kind=pool["pool_kind"],
                period_limit=None, provider_view=None, holds=0,
                safety_margin=pool["safety_margin"],
                effective_available=None, baseline_at=None,
                baseline_origin=None,
            )
        anchor = self._conn.execute(
            """
            SELECT * FROM pool_anchors WHERE source = ?
            ORDER BY anchor_id DESC LIMIT 1
            """,
            (source,),
        ).fetchone()
        provider_view = None
        baseline_at = None
        origin = None
        if anchor is not None:
            spent = self._conn.execute(
                """
                SELECT COALESCE(SUM(units), 0) FROM spend_events
                WHERE source = ? AND event_id > ?
                """,
                (source, anchor["last_spend_event_id"]),
            ).fetchone()[0]
            provider_view = anchor["baseline_remaining"] - spent
            baseline_at = anchor["baseline_at"]
            origin = anchor["origin"]
        holds_sql = """
            SELECT COALESCE(SUM(rr.reserved_units), 0)
            FROM run_reservations rr
            JOIN ledger_runs lr ON lr.run_id = rr.run_id
            WHERE rr.source = ? AND rr.state = 'held'
              AND lr.status = 'running' AND lr.lease_expires_at > ?
        """
        args: list = [source, _now_iso()]
        if exclude_run is not None:
            holds_sql += " AND rr.run_id != ?"
            args.append(exclude_run)
        holds = self._conn.execute(holds_sql, args).fetchone()[0]
        effective = (provider_view - pool["safety_margin"] - holds
                     if provider_view is not None else None)
        return PoolState(
            source=source, pool_kind=pool["pool_kind"],
            period_limit=pool["period_limit"], provider_view=provider_view,
            holds=holds, safety_margin=pool["safety_margin"],
            effective_available=effective, baseline_at=baseline_at,
            baseline_origin=origin,
        )

    def all_pool_states(self) -> list[PoolState]:
        rows = self._conn.execute(
            "SELECT source FROM quota_pools WHERE active = 1 ORDER BY source"
        ).fetchall()
        return [s for r in rows if (s := self.pool_state(r["source"]))]

    # -- runs -------------------------------------------------------------------

    def begin_shadow_run(self, *, trigger: str) -> str:
        run_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        self._conn.execute(
            """
            INSERT INTO ledger_runs
                (run_id, started_at, lease_expires_at, trigger, status)
            VALUES (?, ?, ?, ?, 'running')
            """,
            (run_id, now, now, trigger),
        )
        return run_id

    def begin_run(self, *, trigger: str,
                  lease_minutes: int = 20) -> str | None:
        """Acquire the single-run lease (CAS: insert-unless-live-run).

        Returns None when another unexpired run holds the lease — the
        caller exits cleanly ('another run is active'). Dead runs stop
        blocking automatically when their lease expires; heartbeat()
        extends it after each search.
        """
        run_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).replace(microsecond=0)
        expires = (now + timedelta(minutes=lease_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = self._conn.execute(
            """
            INSERT INTO ledger_runs
                (run_id, started_at, lease_expires_at, trigger, status)
            SELECT ?, ?, ?, ?, 'running'
            WHERE NOT EXISTS (
                SELECT 1 FROM ledger_runs
                WHERE status = 'running' AND lease_expires_at > ?
            )
            """,
            (run_id, now_iso, expires, trigger, now_iso),
        )
        return run_id if cur.rowcount == 1 else None

    def heartbeat(self, run_id: str, *, lease_minutes: int = 20) -> None:
        expires = (datetime.now(timezone.utc)
                   + timedelta(minutes=lease_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        self._conn.execute(
            """
            UPDATE ledger_runs SET lease_expires_at = ?
            WHERE run_id = ? AND status = 'running'
            """,
            (expires, run_id),
        )

    def expire_orphans(self) -> int:
        """Mark dead 'running' runs as abandoned (hygiene only — expired
        leases already stop counting in every availability query)."""
        cur = self._conn.execute(
            """
            UPDATE ledger_runs SET status = 'abandoned', finished_at = ?
            WHERE status = 'running' AND lease_expires_at <= ?
            """,
            (_now_iso(), _now_iso()),
        )
        return cur.rowcount or 0

    # -- reservations (M2 enforcement, on the CAS proven by
    #    scripts/probe_ledger_cas.py: 6/6 races, exactly one winner) ----------

    def reserve(self, run_id: str, search_id: str, cost) -> bool:
        """Reserve a CostVector for one search. All-or-nothing: if any
        line fails its guard, every line already held for this
        (run, search) flips to 'skipped' and False returns.

        The SQL guard re-verifies raw availability even though the
        planner pre-checked — a concurrent local/manual run can never
        oversubscribe (GH Actions' concurrency group only serializes CI).
        """
        now = _now_iso()
        for line in cost.lines:
            pool = self._conn.execute(
                "SELECT * FROM quota_pools WHERE source = ? AND active = 1",
                (line.source,),
            ).fetchone()
            if pool is None or pool["pool_kind"] == "rate_only":
                # Unmetered (or unpooled, e.g. break-glass sources kept
                # out of v1 pools): record the hold for accounting; no
                # scarcity to guard.
                cur = self._conn.execute(
                    """
                    INSERT INTO run_reservations
                        (run_id, search_id, source, kind, reserved_units,
                         state, created_at)
                    VALUES (?, ?, ?, ?, ?, 'held', ?)
                    """,
                    (run_id, search_id, line.source, line.kind,
                     line.units, now),
                )
            elif pool["pool_kind"] == "per_run":
                cap = pool["per_run_cap"] or 0
                cur = self._conn.execute(
                    """
                    INSERT INTO run_reservations
                        (run_id, search_id, source, kind, reserved_units,
                         state, created_at)
                    SELECT ?, ?, ?, ?, ?, 'held', ?
                    WHERE ? <= ? - COALESCE((
                        SELECT SUM(rr.reserved_units) FROM run_reservations rr
                        WHERE rr.run_id = ? AND rr.source = ?
                          AND rr.state = 'held'), 0)
                    """,
                    (run_id, search_id, line.source, line.kind, line.units,
                     now, line.units, cap, run_id, line.source),
                )
            else:  # monthly
                per_search = pool["per_search_cap"]
                if per_search is not None and line.units > per_search:
                    LOG.warning("reserve %s/%s: %d units exceeds "
                                "per_search_cap %d", search_id, line.source,
                                line.units, per_search)
                    self._skip_held(run_id, search_id,
                                    reason="per_search_cap")
                    return False
                cur = self._conn.execute(
                    """
                    INSERT INTO run_reservations
                        (run_id, search_id, source, kind, reserved_units,
                         state, created_at)
                    SELECT ?, ?, ?, ?, ?, 'held', ?
                    WHERE ? <= (
                        SELECT COALESCE((
                            SELECT pa.baseline_remaining FROM pool_anchors pa
                            WHERE pa.source = ?
                            ORDER BY pa.anchor_id DESC LIMIT 1), -1)
                        - COALESCE((
                            SELECT SUM(se.units) FROM spend_events se
                            WHERE se.source = ? AND se.event_id > COALESCE((
                                SELECT pa2.last_spend_event_id
                                FROM pool_anchors pa2 WHERE pa2.source = ?
                                ORDER BY pa2.anchor_id DESC LIMIT 1), 0)), 0)
                        - COALESCE((
                            SELECT SUM(rr.reserved_units)
                            FROM run_reservations rr
                            JOIN ledger_runs lr ON lr.run_id = rr.run_id
                            WHERE rr.source = ? AND rr.state = 'held'
                              AND lr.status = 'running'
                              AND lr.lease_expires_at > ?), 0)
                        - (SELECT qp.safety_margin FROM quota_pools qp
                           WHERE qp.source = ?)
                    )
                    """,
                    (run_id, search_id, line.source, line.kind, line.units,
                     now, line.units, line.source, line.source, line.source,
                     line.source, now, line.source),
                )
            if (cur.rowcount or 0) != 1:
                LOG.info("reserve %s/%s/%s: pool short — skipping search",
                         search_id, line.source, line.kind)
                self._skip_held(run_id, search_id, reason="pool_short")
                # The failed line never inserted a row — record it as
                # skipped explicitly so the digest can say WHICH pool
                # was short (skip-and-notify needs the receipt).
                self._conn.execute(
                    """
                    INSERT INTO run_reservations
                        (run_id, search_id, source, kind, reserved_units,
                         state, skip_reason, created_at)
                    VALUES (?, ?, ?, ?, ?, 'skipped', 'pool_short', ?)
                    """,
                    (run_id, search_id, line.source, line.kind,
                     line.units, now),
                )
                return False
        return True

    def _skip_held(self, run_id: str, search_id: str, *, reason: str) -> None:
        self._conn.execute(
            """
            UPDATE run_reservations SET state = 'skipped', skip_reason = ?
            WHERE run_id = ? AND search_id = ? AND state = 'held'
            """,
            (reason, run_id, search_id),
        )

    def reserved_units(self, run_id: str, search_id: str, source: str) -> int:
        """Total held units for one (run, search, source) — the budget
        GuardedClient enforces (primary + contingency combined: the
        fallback path re-uses whatever the primary didn't spend, still
        inside the quoted total for that source)."""
        return self._conn.execute(
            """
            SELECT COALESCE(SUM(reserved_units), 0) FROM run_reservations
            WHERE run_id = ? AND search_id = ? AND source = ?
              AND state = 'held'
            """,
            (run_id, search_id, source),
        ).fetchone()[0]

    def settle(self, run_id: str, search_id: str) -> None:
        """Backfill used_units from spend_events and close the holds.

        Spend attributes to the PRIMARY line first (up to its
        reservation); only the overflow lands on the contingency line —
        so per-row used_units sums correctly instead of duplicating the
        total into both kinds. Unused contingency -> 'released';
        everything else -> 'consumed'. Idempotent (only touches 'held').
        """
        spend_sq = """
            COALESCE((SELECT SUM(se.units) FROM spend_events se
                      WHERE se.run_id = run_reservations.run_id
                        AND se.search_id = run_reservations.search_id
                        AND se.source = run_reservations.source), 0)
        """
        self._conn.execute(
            f"""
            UPDATE run_reservations
            SET used_units = MIN(reserved_units, {spend_sq}),
                state = 'consumed'
            WHERE run_id = ? AND search_id = ? AND state = 'held'
              AND kind = 'primary'
            """,
            (run_id, search_id),
        )
        self._conn.execute(
            f"""
            UPDATE run_reservations
            SET used_units = MAX(0, {spend_sq} - COALESCE((
                    SELECT SUM(p.reserved_units) FROM run_reservations p
                    WHERE p.run_id = run_reservations.run_id
                      AND p.search_id = run_reservations.search_id
                      AND p.source = run_reservations.source
                      AND p.kind = 'primary'), 0)),
                state = CASE
                    WHEN MAX(0, {spend_sq} - COALESCE((
                        SELECT SUM(p2.reserved_units) FROM run_reservations p2
                        WHERE p2.run_id = run_reservations.run_id
                          AND p2.search_id = run_reservations.search_id
                          AND p2.source = run_reservations.source
                          AND p2.kind = 'primary'), 0)) = 0
                    THEN 'released' ELSE 'consumed'
                END
            WHERE run_id = ? AND search_id = ? AND state = 'held'
              AND kind = 'contingency'
            """,
            (run_id, search_id),
        )

    def finalize_run(self, run_id: str, status: str) -> None:
        self._conn.execute(
            """
            UPDATE ledger_runs SET status = ?, finished_at = ?
            WHERE run_id = ? AND status = 'running'
            """,
            (status, _now_iso(), run_id),
        )

    # -- spend -----------------------------------------------------------------

    def record_spend(self, *, run_id: str | None, search_id: str | None,
                     source: str, units: int, op: str) -> int:
        """Charge BEFORE the call. Fail closed: if this insert fails, the
        caller must not make the HTTP call (never spend unrecorded).
        Returns the event_id for mark()."""
        cur = self._conn.execute(
            """
            INSERT INTO spend_events
                (run_id, search_id, source, units, op, result, spent_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (run_id, search_id, source, units, op, _now_iso()),
        )
        rowid = cur.lastrowid
        if rowid is None:  # Turso HTTP cursor may not surface lastrowid
            rowid = self._conn.execute(
                "SELECT MAX(event_id) FROM spend_events"
            ).fetchone()[0]
        return int(rowid)

    def mark(self, event_id: int, result: str) -> None:
        self._conn.execute(
            "UPDATE spend_events SET result = ? WHERE event_id = ?",
            (result, event_id),
        )

    def spent_by_run(self, run_id: str) -> dict[tuple[str, str], int]:
        """(search_id, source) -> units, for summaries/settle."""
        out: dict[tuple[str, str], int] = {}
        for r in self._conn.execute(
            """
            SELECT search_id, source, COALESCE(SUM(units), 0) AS units
            FROM spend_events WHERE run_id = ?
            GROUP BY search_id, source
            """,
            (run_id,),
        ).fetchall():
            out[(r["search_id"], r["source"])] = r["units"]
        return out


class GuardedClient:
    """Proxy around a source client. Metered methods charge the ledger
    BEFORE invoking the wrapped method; everything else passes through.

    shadow=True (M1): record only — never refuses, so scan behavior is
    byte-identical to unguarded. shadow=False (M2): refuse at 0 with
    QuotaExceeded, decrementing a per-(run,search,source) budget.
    """

    def __init__(self, inner, *, ledger: QuotaLedger, source: str,
                 run_id: str | None, search_id: str | None,
                 shadow: bool = True, budget_units: int | None = None):
        self._inner = inner
        self._ledger = ledger
        self._source = source
        self._run_id = run_id
        self._search_id = search_id
        self._shadow = shadow
        self._remaining = budget_units
        self._metered = METERED.get(source, {})

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        units = self._metered.get(name)
        if units is None or not callable(attr):
            return attr

        def guarded(*args, **kwargs):
            if not self._shadow:
                if self._remaining is not None and self._remaining < units:
                    raise QuotaExceeded(
                        f"{self._source}.{name}: budget exhausted "
                        f"(remaining={self._remaining}, needs={units})")
            event_id = self._ledger.record_spend(
                run_id=self._run_id, search_id=self._search_id,
                source=self._source, units=units, op=name)
            if self._remaining is not None:
                self._remaining -= units
            try:
                result = attr(*args, **kwargs)
            except Exception as exc:
                label = "429" if "429" in str(exc) else "error"
                try:
                    self._ledger.mark(event_id, label)
                except Exception:  # noqa: BLE001 — marking must not mask the real error
                    LOG.warning("quota: mark(%s) failed post-error", event_id)
                raise
            try:
                empty = _looks_empty(result)
                self._ledger.mark(event_id, "empty" if empty else "ok")
            except Exception:  # noqa: BLE001
                LOG.warning("quota: mark(%s) failed post-call", event_id)
            return result

        return guarded

    # Context-manager protocol can't be caught by __getattr__ (dunder
    # lookup happens on the type), so forward explicitly. __enter__
    # returns a guarded wrapper around whatever the inner one returns.
    def __enter__(self):
        entered = self._inner.__enter__()
        if entered is self._inner:
            return self
        return GuardedClient(entered, ledger=self._ledger,
                             source=self._source, run_id=self._run_id,
                             search_id=self._search_id, shadow=self._shadow,
                             budget_units=self._remaining)

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)


def _looks_empty(result) -> bool:
    """Best-effort 'the call worked but returned nothing' classification
    for accounting (a spent-but-empty call is a signal worth counting)."""
    for attr in ("best_flights", "options", "quotes", "entries"):
        seq = getattr(result, attr, None)
        if seq is not None:
            return len(seq) == 0
    return False
