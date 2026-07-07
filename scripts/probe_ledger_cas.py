#!/usr/bin/env python
"""Probe: is a WHERE-guarded INSERT...SELECT a safe CAS on Turso's HTTP API?

The M2 reservation design depends on single-statement compare-and-swap
(no transactions on the autocommit Hrana pipeline). This probe races two
separate TursoConnections on a synthetic pool with room for exactly ONE
reservation; a correct backend admits exactly one winner.

Uses a synthetic source name so production pools are untouched; cleans
up its rows afterward. Run: python scripts/probe_ledger_cas.py
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(dotenv_path=REPO / ".env")

from lib import db as db_mod  # noqa: E402

SRC = "castest-probe"
GUARDED_INSERT = """
INSERT INTO run_reservations
    (run_id, search_id, source, kind, reserved_units, state, created_at)
SELECT ?, 'probe', ?, 'primary', 6, 'held', '2026-01-01T00:00:00Z'
WHERE 6 <= (
    SELECT 10 - COALESCE((SELECT SUM(rr.reserved_units)
                          FROM run_reservations rr
                          WHERE rr.source = ? AND rr.state = 'held'), 0)
)
"""


def attempt(run_id: str, results: dict) -> None:
    # Separate connection per thread — the race we care about.
    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        cur = conn.execute(GUARDED_INSERT, (run_id, SRC, SRC))
        results[run_id] = cur.rowcount


def main() -> int:
    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        db_mod.ensure_schema(conn)
        conn.execute("DELETE FROM run_reservations WHERE source = ?", (SRC,))

    wins_history = []
    ROUNDS = 6
    for i in range(ROUNDS):
        with db_mod.connect(REPO / "data" / "tracker.db") as conn:
            conn.execute("DELETE FROM run_reservations WHERE source = ?", (SRC,))
        results: dict = {}
        barrier = threading.Barrier(2)

        def racer(rid):
            barrier.wait()
            attempt(rid, results)

        t1 = threading.Thread(target=racer, args=(f"r{i}a",))
        t2 = threading.Thread(target=racer, args=(f"r{i}b",))
        t1.start(); t2.start(); t1.join(); t2.join()

        with db_mod.connect(REPO / "data" / "tracker.db") as conn:
            held = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(reserved_units), 0) "
                "FROM run_reservations WHERE source = ? AND state = 'held'",
                (SRC,),
            ).fetchone()
        wins = held[0]
        wins_history.append(wins)
        print(f"round {i}: rowcounts={results}  held_rows={held[0]} "
              f"held_units={held[1]}  -> {'OK' if wins == 1 else 'VIOLATION'}")

    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        conn.execute("DELETE FROM run_reservations WHERE source = ?", (SRC,))

    ok = all(w == 1 for w in wins_history)
    print(f"\nVERDICT: {'CAS SAFE — exactly one winner in all rounds' if ok else 'UNSAFE — oversubscription observed'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
