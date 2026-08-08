#!/usr/bin/env python
"""Render deal-card PNGs (M3, D8) for recent verified+ deals.

Usage: python scripts/render_cards.py [--limit 5] [--out out/cards]
Posting is manual-from-phone in v1 — this just drops the PNGs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=REPO / ".env")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--out", default="out/cards")
    args = ap.parse_args()

    from lib import db as db_mod
    from lib.dealcard import CardData, render_deal_card
    from lib.deals_db import ensure_deals_schema

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        ensure_deals_schema(conn)
        deals = conn.execute(
            """
            SELECT * FROM deals
            WHERE status IN ('verified','queued','approved','published')
            ORDER BY created_at DESC LIMIT ?
            """, (args.limit,)).fetchall()
        for d in deals:
            spark = [r["price"] for r in conn.execute(
                """
                SELECT MIN(price) AS price FROM fare_observations
                WHERE origin = ? AND dest = ?
                GROUP BY substr(observed_at, 1, 10)
                ORDER BY substr(observed_at, 1, 10)
                """, (d["origin"], d["dest"])).fetchall()]
            dates = (d["sample_dates"] or "").replace("..", " → ")
            card = CardData(
                origin=d["origin"], dest=d["dest"], price=d["price"],
                currency=d["currency"], normal=d["baseline_median"],
                pct_below=d["pct_below"],
                dates_line=dates or "fechas flexibles",
                carrier=None)
            path = out / f"deal_{d['id']}_{d['origin']}_{d['dest']}.png"
            render_deal_card(card, spark, path)
            print(f"wrote {path}")
            n += 1
    print(f"{n} card(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
