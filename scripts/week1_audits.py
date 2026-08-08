#!/usr/bin/env python
"""Week-1 audits (M1 DoD + D1 reversal triggers), runnable any time.

Produces a markdown report from the live DB:
  1. Aviasales cache freshness per route — found_at age at observation
     time (the D1 "is the free cache fresh enough for Spanish origins"
     audit; market=es is implicit in the origins).
  2. price_insights coverage — share of verifications whose SerpAPI
     response carried a typical range (the D2 cold-start baseline rail).
  3. Skyscanner-everywhere probe verdict — read from the source registry
     + spend history (flights_sky is parked: PerimeterX captcha, see
     lib/sources.py notes).
  4. Queue discipline — deals/day vs the 15 cap; rejection reasons.
  5. VLC/ALC excellent-deal rate (D0b week-8 checkpoint feed).

Usage: python scripts/week1_audits.py [--out docs/notes/audits/week1.md]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=REPO / ".env")


def _age_hours(found_at: str | None, observed_at: str) -> float | None:
    if not found_at:
        return None
    try:
        f = datetime.fromisoformat(found_at.replace("Z", "+00:00"))
        o = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if f.tzinfo is None:
            f = f.replace(tzinfo=timezone.utc)
        return max(0.0, (o - f).total_seconds() / 3600.0)
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from lib import db as db_mod
    from lib.deals_db import ensure_deals_schema

    lines: list[str] = [f"# Week-1 audits — generated {datetime.now(timezone.utc):%Y-%m-%d %H:%MZ}", ""]

    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        ensure_deals_schema(conn)

        # 1. Cache freshness per origin.
        lines.append("## 1. Aviasales cache freshness (found_at age at observation)")
        rows = conn.execute(
            "SELECT origin, found_at, observed_at FROM fare_observations "
            "WHERE source='aviasales'").fetchall()
        by_origin: dict[str, list[float]] = {}
        missing = 0
        for r in rows:
            age = _age_hours(r["found_at"], r["observed_at"])
            if age is None:
                missing += 1
            else:
                by_origin.setdefault(r["origin"], []).append(age)
        if not by_origin:
            lines.append("- no observations yet")
        for origin, ages in sorted(by_origin.items()):
            lines.append(
                f"- {origin}: n={len(ages)}, median age "
                f"{statistics.median(ages):.1f}h, p90 "
                f"{sorted(ages)[int(len(ages) * 0.9) - 1 if len(ages) > 1 else 0]:.1f}h")
        lines.append(f"- rows without found_at: {missing}")
        lines.append("- D1 trigger: if the cache is too thin/stale, promote "
                     "paid discovery (SearchAPI calendar rectangles).")
        lines.append("")

        # 2. price_insights coverage across verifications.
        lines.append("## 2. Google price_insights coverage")
        deals = conn.execute(
            "SELECT verification_refs, baseline_median, status FROM deals "
            "WHERE verification_refs IS NOT NULL").fetchall()
        verified = [d for d in deals if d["status"] not in ("candidate",)]
        with_baseline = [d for d in verified if d["baseline_median"]]
        lines.append(f"- verifications recorded: {len(verified)}; with a "
                     f"baseline number: {len(with_baseline)}")
        lines.append("- (per-deal insights presence rides "
                     "verification_refs/baseline_median; full per-response "
                     "coverage lands with more volume)")
        lines.append("")

        # 3. Skyscanner-everywhere probe verdict.
        lines.append("## 3. Skyscanner-everywhere probe verdict")
        from lib.sources import spec
        fs = spec("flights_sky")
        lines.append(f"- flights_sky enabled={fs.enabled}; note: {fs.note}")
        n_fs = conn.execute(
            "SELECT COUNT(*) FROM spend_events WHERE source='flights_sky'"
        ).fetchone()[0]
        lines.append(f"- spend_events rows: {n_fs}")
        lines.append("- VERDICT: parked (PerimeterX captcha wall, 2026-07-14 "
                     "live test); OTA coverage rides SerpAPI booking_options "
                     "instead. Revisit only if a no-card Hard-Limit proxy "
                     "appears.")
        lines.append("")

        # 4. Queue discipline.
        lines.append("## 4. Queue discipline (cap 15/day)")
        daily = conn.execute(
            "SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM deals "
            "GROUP BY d ORDER BY d DESC LIMIT 14").fetchall()
        worst = max((r["n"] for r in daily), default=0)
        for r in daily:
            lines.append(f"- {r['d']}: {r['n']} deal(s)")
        lines.append(f"- max/day so far: {worst} (cap 15) — "
                     + ("OK" if worst <= 15 else "OVER, tighten gates"))
        rej = conn.execute(
            "SELECT reason, COUNT(*) AS n FROM rejections GROUP BY reason"
        ).fetchall()
        lines.append("- rejection reasons: "
                     + (", ".join(f"{r['reason']}={r['n']}" for r in rej)
                        or "none yet"))
        lines.append("")

        # 5. VLC/ALC excellent-deal rate (D0b checkpoint feed).
        lines.append("## 5. VLC/ALC excellent-deal rate (D0b week-8 feed)")
        beach = conn.execute(
            "SELECT origin, COUNT(*) AS n FROM deals "
            "WHERE origin IN ('VLC','ALC') AND status IN "
            "('verified','queued','approved','published') "
            "GROUP BY origin").fetchall()
        for r in beach:
            lines.append(f"- {r['origin']}: {r['n']} verified+ deal(s)")
        if not beach:
            lines.append("- none yet")
        lines.append("- D0b trigger: <3 genuinely excellent deals/week from "
                     "VLC/ALC by week 8 → flip beachhead to MAD/BCN before "
                     "audience-building.")

    report = "\n".join(lines) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
