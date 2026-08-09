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
        lines.append("")

        # 6. The long-haul funnel — the question the product lives on.
        from lib.dealconfig import load_deal_config
        from lib.dealgate import classify_route
        config = load_deal_config()

        lines.append("## 6. Long-haul funnel (is this a Ryanair alerts app?)")
        lines.append("")
        lines.append("Nobody pays for EUR 37 hops to Turin. Every stage below "
                     "must keep a long-haul share, not just the top of the "
                     "funnel: reach without candidates means the gate is "
                     "biased; candidates without publishes means "
                     "verification is.")
        lines.append("")
        stages: list[tuple[str, list[str]]] = [
            ("reach (observed)", [r["dest"] for r in conn.execute(
                "SELECT dest FROM fare_observations "
                "WHERE observed_at >= date('now','-7 day')").fetchall()]),
            ("candidates", [r["dest"] for r in conn.execute(
                "SELECT dest FROM deals "
                "WHERE created_at >= date('now','-7 day')").fetchall()]),
            ("verified+", [r["dest"] for r in conn.execute(
                "SELECT dest FROM deals WHERE status IN "
                "('verified','queued','approved','published') "
                "AND created_at >= date('now','-7 day')").fetchall()]),
            ("published", [r["dest"] for r in conn.execute(
                "SELECT dest FROM deals WHERE status='published' "
                "AND created_at >= date('now','-7 day')").fetchall()]),
        ]
        lines.append("| stage | total | long | medium | intra_eu | long % |")
        lines.append("|---|---|---|---|---|---|")
        long_share: dict[str, float | None] = {}
        for label, dests in stages:
            counts = {"long": 0, "medium": 0, "intra_eu": 0}
            for d in dests:
                cls = classify_route(d, config)
                if cls in counts:
                    counts[cls] += 1
            total = len(dests)
            pct = (100.0 * counts["long"] / total) if total else None
            long_share[label] = pct
            lines.append(
                f"| {label} | {total} | {counts['long']} | "
                f"{counts['medium']} | {counts['intra_eu']} | "
                + (f"{pct:.0f}%" if pct is not None else "—") + " |")
        lines.append("")
        pub = long_share.get("published")
        cand = long_share.get("candidates")
        if pub is not None and pub < 30:
            lines.append(f"- **TRIGGER: only {pub:.0f}% of published deals are "
                         "long-haul.** Below ~30% the alert stream reads as a "
                         "budget-airline feed. Look at WHICH stage collapses "
                         "in the table above before touching thresholds.")
        elif cand is not None and cand < 15:
            lines.append(f"- TRIGGER: long-haul is {cand:.0f}% of candidates — "
                         "the GATE is the bottleneck (class cross-section "
                         "medians favour cheap short hops), not discovery.")
        else:
            lines.append("- long-haul share is holding across the funnel.")
        lines.append("")
        unclassified = conn.execute(
            "SELECT DISTINCT dest FROM fare_observations "
            "WHERE observed_at >= date('now','-7 day')").fetchall()
        unknown = sorted({r["dest"] for r in unclassified
                          if classify_route(r["dest"], config) == "unclassified"})
        lines.append(f"- unclassified destination codes seen this week: "
                     f"{len(unknown)}"
                     + (f" — {', '.join(unknown[:25])}" if unknown else ""))
        lines.append("  (each one is a route the gate silently skipped; add "
                     "them to routes/route_classes.yaml)")
        lines.append("")

        # 7. The insights floor, measured while disabled.
        lines.append("## 7. Route-specific floor (shadow measurement)")
        lines.append("")
        lines.append("`detector.insights_floor` compares a fare to ITS OWN "
                     "Google typical range instead of a class median. It is "
                     f"currently **{'ON' if config.insights_floor else 'OFF'}"
                     "**; while OFF every verification still records whether "
                     "it WOULD have passed, so the cost of switching it on is "
                     "measurable before the switch.")
        lines.append("")
        passed = failed = unknown_floor = 0
        for d in conn.execute(
                "SELECT verification_refs FROM deals "
                "WHERE verification_refs IS NOT NULL").fetchall():
            try:
                refs = json.loads(d["verification_refs"])
            except (TypeError, ValueError):
                continue
            fl = (refs or {}).get("insights_floor")
            if not isinstance(fl, dict):
                continue
            if fl.get("passed") is True:
                passed += 1
            elif fl.get("passed") is False:
                failed += 1
            else:
                unknown_floor += 1
        measured = passed + failed
        lines.append(f"- would PASS: {passed} · would FAIL: {failed} · "
                     f"no Google range (unknowable): {unknown_floor}")
        if measured:
            lines.append(
                f"- switching it on would have cut {failed} of {measured} "
                f"verified deals ({100.0 * failed / measured:.0f}%)")
            if unknown_floor:
                lines.append(
                    f"- ALSO decide what an unknowable range means: {unknown_floor} "
                    "deal(s) had no typical range at all. Today they publish.")
        else:
            lines.append("- not enough verifications yet — needs a week of runs")
        lines.append("")

        # 8. Per-project spend (the two projects share this database).
        lines.append("## 8. Spend by project (30 days)")
        lines.append("")
        spend = conn.execute(
            "SELECT source, COUNT(*) AS n, SUM(units) AS u FROM spend_events "
            "WHERE spent_at >= date('now','-30 day') "
            "GROUP BY source ORDER BY source").fetchall()
        vz = [r for r in spend if r["source"].endswith("_vz")
              or r["source"] in ("anthropic", "telegram", "resend")]
        tracker = [r for r in spend if r not in vz]
        for label, group in (("Vuelazo", vz), ("flight_scans (NBO tracker)", tracker)):
            lines.append(f"- **{label}**: "
                         + (", ".join(f"{r['source']}={r['u'] or 0}u"
                                      for r in group) or "no spend"))
        lines.append("- the tracker must stay on free tiers; any Vuelazo "
                     "source appearing in ITS row means the separation "
                     "leaked (check which env keys are set).")
        lines.append("- NOTE: spend before 2026-08-09 predates the split — "
                     "Vuelazo ran on the tracker's `serpapi`/`aviasales` ids, "
                     "so those older units are mixed. The rows separate "
                     "cleanly from the first run after the split.")

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
