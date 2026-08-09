#!/usr/bin/env python
"""SEO route-page generation (M4b, D6): gate + Claude intros.

For every (origin, dest) with fare history:
  * status 'published' when verified observations >= min_observations
    (the detector's own bar), else 'noindex'.
  * For published routes: generate the Claude intro ONCE, refresh only
    when older than 90 days (quarterly). Ledger-metered (anthropic).

Usage: python scripts/gen_seo_intros.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=REPO / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOG = logging.getLogger("seo")

SEARCH_ID = "vuelazo-seo"
REFRESH_DAYS = 90

CITY = {"MAD": "Madrid", "BCN": "Barcelona", "VLC": "València",
        "ALC": "Alacant"}

# Destination display names for the intro prompt — the model is told to
# use ONLY the DATOS block, so feeding it 'NBO (NBO)' would bake airport
# codes into ~40-60 launch pages (Spanish output is product surface,
# non-negotiable #8). Mirrors web/src/lib/hubs.ts DEST_NAMES.
DEST_CITY = {
    "LON": "Londres", "PAR": "París", "ROM": "Roma", "MIL": "Milán",
    "AMS": "Ámsterdam", "BER": "Berlín", "BRU": "Bruselas", "VIE": "Viena",
    "PRG": "Praga", "BUD": "Budapest", "ATH": "Atenas", "LIS": "Lisboa",
    "OPO": "Oporto", "DUB": "Dublín", "EDI": "Edimburgo",
    "CPH": "Copenhague", "ARN": "Estocolmo", "ZRH": "Zúrich",
    "IST": "Estambul", "RAK": "Marrakech", "CMN": "Casablanca",
    "TUN": "Túnez", "CAI": "El Cairo", "TLV": "Tel Aviv", "DXB": "Dubái",
    "NYC": "Nueva York", "MIA": "Miami", "CUN": "Cancún",
    "MEX": "Ciudad de México", "HAV": "La Habana", "PUJ": "Punta Cana",
    "BOG": "Bogotá", "EZE": "Buenos Aires", "SCL": "Santiago",
    "LIM": "Lima", "BKK": "Bangkok", "TYO": "Tokio", "DEL": "Delhi",
    "MRS": "Marsella", "NTE": "Nantes", "SVQ": "Sevilla", "PMI": "Palma",
    "IBZ": "Ibiza", "AGP": "Málaga", "BIO": "Bilbao", "OVD": "Asturias",
    "NAP": "Nápoles", "VCE": "Venecia", "FCO": "Roma", "NBO": "Nairobi",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=60,
                    help="max intros per run (launch cohort ~40-60, D6)")
    ap.add_argument("--trigger", default="local")
    args = ap.parse_args()

    from lib import db as db_mod
    from lib.baselines import mature_routes, route_baseline
    from lib.clients import guard_clients
    from lib.dealconfig import load_deal_config
    from lib.deals_db import ensure_deals_schema
    from lib.drafting import AnthropicDraftClient, load_template
    from lib.planner import CostLine, CostVector
    from lib.quota import SCOPE_VUELAZO, QuotaLedger
    from run_deals import _ensure_service_anchor

    config = load_deal_config()
    template = load_template(REPO / "templates" / "seo_intro_es.md")

    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        db_mod.ensure_schema(conn)
        ensure_deals_schema(conn)

        # 1. Gate every measured route into published / noindex.
        mature = set(mature_routes(
            conn, window_days=config.baseline_window_days,
            min_observations=config.min_observations))
        routes = conn.execute(
            "SELECT DISTINCT origin, dest FROM fare_observations").fetchall()
        published, noindex = 0, 0
        for r in routes:
            key = (r["origin"], r["dest"])
            status = "published" if key in mature else "noindex"
            conn.execute(
                """
                INSERT INTO seo_pages (origin, dest, status, last_generated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(origin, dest) DO UPDATE SET
                    status = excluded.status,
                    last_generated = excluded.last_generated
                """,
                (r["origin"], r["dest"], status, _now_iso()))
            published += status == "published"
            noindex += status == "noindex"
        print(f"gate: {published} published, {noindex} noindex "
              f"(bar: {config.min_observations} verified obs / "
              f"{config.baseline_window_days}d)")

        # 2. Intros for published routes missing/stale ones.
        stale_cutoff = (datetime.now(timezone.utc)
                        - timedelta(days=REFRESH_DAYS)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        need = conn.execute(
            """
            SELECT origin, dest FROM seo_pages
            WHERE status = 'published'
              AND (intro_es IS NULL OR intro_generated_at < ?)
            LIMIT ?
            """, (stale_cutoff, args.limit)).fetchall()
        if not need:
            print("intros: nothing to generate")
            return 0
        if args.dry_run:
            for r in need:
                print(f"[dry] would draft intro {r['origin']}->{r['dest']}")
            return 0

        try:
            drafter = AnthropicDraftClient.from_env(
                model=config.draft_model, max_tokens=400)
        except RuntimeError as exc:
            print(f"ANTHROPIC_API_KEY missing — intros deferred ({exc}); "
                  f"pages run on data blocks meanwhile")
            return 0

        ledger = QuotaLedger(conn)
        ledger.seed_pools()
        ledger.expire_orphans()
        run_id = ledger.begin_run(trigger=args.trigger, scope=SCOPE_VUELAZO)
        if run_id is None:
            print("another run holds the lease — try later")
            return 0
        status = "ok"
        try:
            _ensure_service_anchor(ledger, "anthropic")
            cost = CostVector(lines=(
                CostLine("anthropic", len(need), "primary", "seo intros"),))
            if not ledger.reserve(run_id, SEARCH_ID, cost,
                                  enforce_per_search_cap=False):
                print("anthropic pool short — fewer intros this run")
                return 0
            guarded = guard_clients({"anthropic": drafter}, ledger=ledger,
                                    run_id=run_id, search_id=SEARCH_ID,
                                    shadow=False)
            done = 0
            for r in need:
                origin, dest = r["origin"], r["dest"]
                bl = route_baseline(
                    conn, origin=origin, dest=dest,
                    window_days=config.baseline_window_days,
                    min_observations=config.min_observations)
                best = conn.execute(
                    "SELECT MIN(price) FROM fare_observations WHERE "
                    "origin = ? AND dest = ? AND observed_at >= ?",
                    (origin, dest,
                     (datetime.now(timezone.utc) - timedelta(days=14)
                      ).strftime("%Y-%m-%d"))).fetchone()[0]
                month = conn.execute(
                    "SELECT substr(depart_date,1,7) AS m, MIN(price) AS p "
                    "FROM fare_observations WHERE origin = ? AND dest = ? "
                    "GROUP BY m HAVING COUNT(*) >= 3 ORDER BY p LIMIT 1",
                    (origin, dest)).fetchone()
                fields = {
                    "origin": origin,
                    "origin_city": CITY.get(origin, origin),
                    "dest": dest,
                    "dest_city": DEST_CITY.get(dest, dest),
                    "normal": bl.median if bl else "sin dato",
                    "n_obs": bl.n if bl else 0,
                    "best_price": best or "sin dato",
                    "best_month": month["m"] if month else "sin dato",
                }
                result = guarded["anthropic"].draft(fields=fields,
                                                    template=template)
                conn.execute(
                    "UPDATE seo_pages SET intro_es = ?, "
                    "intro_generated_at = ? WHERE origin = ? AND dest = ?",
                    (result.text, _now_iso(), origin, dest))
                done += 1
                print(f"intro {origin}->{dest} ({len(result.text)} chars)")
            print(f"intros: {done} generated "
                  f"[template {template.version}]")
        except Exception:  # noqa: BLE001
            LOG.exception("seo intro run failed")
            status = "failed"
        finally:
            ledger.settle(run_id, SEARCH_ID)
            ledger.finalize_run(run_id, status)
        return 0 if status == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
