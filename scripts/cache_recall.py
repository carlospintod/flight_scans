#!/usr/bin/env python
"""Is the free cache good enough to NOMINATE deals? Measured, not assumed.

The whole cheap architecture rests on one unproven assumption: the
Travelpayouts/Aviasales cache nominates real fares and paid Google calls
merely confirm them. If the cache cannot see what Google sees, no API
tier fixes it — the architecture is wrong, not underfunded, and that is
a EUR 0 question.

The 2026-08-13 research put a documented floor under the doubt: cached
Travelpayouts data is 2-7 days old and Travelpayouts recommends caching
a further 24h, while error fares stay bookable for minutes to hours.

Three measurements, ALL from data already in the database — no API
calls, no third-party scraping:

  A. FIDELITY   Every verification on record: what the cache claimed vs
                what Google actually charged. This is the cache's
                error distribution, and it is already large enough to
                read.
  B. COVERAGE   Routes where Explore (Google's own data) found a fare:
                does the cache carry that route at all, and at what
                price? A route the cache prices 60% high can never
                become a candidate, so its deals are invisible to us at
                any budget.
  C. FRESHNESS  found_at age on cached rows — the structural limit on
                what class of fare the cache could ever catch.

    python scripts/cache_recall.py [--out docs/notes/audits/recall.md]
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

# A cached fare within this % of the live one would have survived the
# pipeline's tolerance and become a real candidate.
NEAR_PCT = 25.0


def _age_hours(found_at: str | None, observed_at: str) -> float | None:
    if not found_at:
        return None
    try:
        f = datetime.fromisoformat(str(found_at).replace("Z", "+00:00"))
        o = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if f.tzinfo is None:
        f = f.replace(tzinfo=timezone.utc)
    if o.tzinfo is None:
        o = o.replace(tzinfo=timezone.utc)
    return max(0.0, (o - f).total_seconds() / 3600.0)


def _pctile(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(len(s) * q)))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from lib import db as db_mod
    from lib.deals_db import ensure_deals_schema

    L: list[str] = [
        f"# Cache recall — generated {datetime.now(timezone.utc):%Y-%m-%d %H:%MZ}",
        "",
        "Can the free Aviasales cache NOMINATE real deals? Everything below "
        "comes from data already collected — no API calls were made.",
        "",
    ]

    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        ensure_deals_schema(conn)

        # -- A. fidelity: cached claim vs live truth ------------------
        L += ["## A. Fidelity — what the cache claimed vs what Google charged", ""]
        gaps: list[float] = []
        rows = conn.execute(
            "SELECT origin, dest, price, verification_refs FROM deals "
            "WHERE verification_refs IS NOT NULL").fetchall()
        detail: list[tuple[str, int, int, float]] = []
        for r in rows:
            try:
                refs = json.loads(r["verification_refs"] or "{}")
            except (TypeError, ValueError):
                continue
            live = refs.get("live_price")
            cached = refs.get("cached_price") or r["price"]
            if not isinstance(live, int) or not cached:
                continue
            gap = (live - cached) / cached * 100.0
            gaps.append(gap)
            detail.append((f"{r['origin']}->{r['dest']}", cached, live, gap))

        if gaps:
            within = sum(1 for g in gaps if abs(g) <= NEAR_PCT)
            usable = sum(1 for g in gaps if g <= 10.0)
            L += [
                f"- verifications with both numbers: **{len(gaps)}**",
                f"- median gap (live vs cached): **{statistics.median(gaps):+.0f}%**",
                f"- p90 gap: **{_pctile(gaps, 0.9):+.0f}%**",
                f"- cached within {NEAR_PCT:.0f}% of live: "
                f"**{within}/{len(gaps)}** ({100*within/len(gaps):.0f}%)",
                f"- cached price actually usable (live <= cached +10%): "
                f"**{usable}/{len(gaps)}** ({100*usable/len(gaps):.0f}%)",
                "",
                "| route | cached | live | gap |",
                "|---|---|---|---|",
            ]
            for name, cached, live, gap in sorted(detail, key=lambda x: -x[3])[:15]:
                L.append(f"| {name} | {cached} € | {live} € | {gap:+.0f}% |")
            L += ["",
                  "**The nomination rate that matters is the last one.** Every "
                  "other cached row costs a verification call to disprove."]
        else:
            L.append("- no verifications with both prices yet")
        L.append("")

        # -- B. coverage: does the cache see what Google sees? --------
        L += ["## B. Coverage — routes Google's Explore found, priced by the cache",
              ""]
        exp = conn.execute(
            "SELECT origin, dest, MIN(price) p FROM fare_observations "
            "WHERE source='explore' GROUP BY origin, dest").fetchall()
        if not exp:
            L.append("- no Explore observations yet — run the pipeline first")
        else:
            covered = near = 0
            lines: list[str] = []
            for e in exp:
                cache_row = conn.execute(
                    "SELECT MIN(price) p FROM fare_observations "
                    "WHERE source!='explore' AND origin=? AND dest=?",
                    (e["origin"], e["dest"])).fetchone()
                cp = cache_row["p"] if cache_row else None
                if cp is None:
                    lines.append(f"| {e['origin']}->{e['dest']} | {e['p']} € "
                                 f"| — | not in cache |")
                    continue
                covered += 1
                gap = (cp - e["p"]) / e["p"] * 100.0
                if abs(gap) <= NEAR_PCT:
                    near += 1
                lines.append(f"| {e['origin']}->{e['dest']} | {e['p']} € | "
                             f"{cp} € | {gap:+.0f}% |")
            L += [
                f"- routes Explore found: **{len(exp)}**",
                f"- also present in the cache: **{covered}/{len(exp)}**",
                f"- cached within {NEAR_PCT:.0f}% of Google's price: "
                f"**{near}/{len(exp)}**",
                "",
                "| route | Google (explore) | cache | gap |",
                "|---|---|---|---|",
            ] + lines[:20]
        L.append("")

        # -- C. freshness --------------------------------------------
        L += ["## C. Freshness — how old cached prices are when we read them", ""]
        ages: list[float] = []
        missing = 0
        for r in conn.execute(
                "SELECT found_at, observed_at FROM fare_observations "
                "WHERE source!='explore' AND observed_at >= date('now','-7 day')"
        ).fetchall():
            a = _age_hours(r["found_at"], r["observed_at"])
            if a is None:
                missing += 1
            else:
                ages.append(a)
        if ages:
            L += [
                f"- rows with a provider timestamp: **{len(ages)}** "
                f"(without: {missing})",
                f"- median age: **{statistics.median(ages):.0f}h**",
                f"- p90 age: **{_pctile(ages, 0.9):.0f}h**",
                "",
                "Documented error-fare bookable windows for comparison: under "
                "30 minutes (Miami-Fortaleza), ~2h (China Southern), ~3h "
                "(Iberia Rio-Paris), ~24h (Cathay Pacific). A cache whose "
                "median age exceeds those windows cannot catch that class of "
                "fare at any price — which is a fact about the SOURCE, not "
                "about our budget.",
            ]
        else:
            L.append("- no cached rows carry a provider timestamp")

    report = "\n".join(L) + "\n"
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report, encoding="utf-8")
        print(f"wrote {p}")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
