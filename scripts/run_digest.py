#!/usr/bin/env python
"""Sunday digest (M3, D3/D4): self-assembles from the week's approvals;
one review; send to the free list.

Usage:
    python scripts/run_digest.py              # assemble + console review + send
    python scripts/run_digest.py --queue      # assemble draft + ntfy, no send (cron)
    python scripts/run_digest.py --send       # approve the latest draft and send
    python scripts/run_digest.py --auto-approve  # assemble + send, no console

Recipients: subscribers (active, not suppressed) + the owner. Every
send carries one-click List-Unsubscribe (per-subscriber token).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=REPO / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOG = logging.getLogger("digest")

SEARCH_ID = "vuelazo-digest"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", action="store_true",
                    help="assemble the draft only (cron mode)")
    ap.add_argument("--send", action="store_true",
                    help="approve the latest draft digest and send it")
    ap.add_argument("--auto-approve", action="store_true")
    ap.add_argument("--trigger", default="local")
    args = ap.parse_args()

    from lib import db as db_mod
    from lib import deals_db, digest as dg
    from lib.clients import guard_clients
    from lib.dealconfig import load_deal_config
    from lib.planner import CostLine, CostVector
    from lib.pushes import push
    from lib.quota import SCOPE_VUELAZO, QuotaLedger
    from lib.resend_api import ResendClient
    from run_deals import _ensure_service_anchor

    config = load_deal_config()
    web_url = (os.environ.get("WEB_URL", "").strip()
               or "https://flight-scans.vercel.app")

    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        db_mod.ensure_schema(conn)
        deals_db.ensure_deals_schema(conn)
        dg.ensure_digest_schema(conn)

        # 1. Assemble (idempotent per week) unless we're in send-only mode.
        if args.send:
            row = conn.execute(
                "SELECT * FROM digests WHERE status IN ('draft','approved') "
                "ORDER BY week_start DESC LIMIT 1").fetchone()
            if row is None:
                print("no digest draft to send")
                return 0
            digest_id = row["id"]
        else:
            digest_id = dg.assemble_digest(conn)
            if digest_id is None:
                print("no approved deals this week — no digest (better none "
                      "than junk, D4)")
                return 0
        row = conn.execute("SELECT * FROM digests WHERE id = ?",
                           (digest_id,)).fetchone()
        print(f"— digest #{digest_id} · week {row['week_start']} · "
              f"{row['n_deals']} deal(s) · {row['status']} —")
        print(row["draft_es"])

        if args.queue:
            push("Vuelazo: digest listo para revisar",
                 f"Semana {row['week_start']}: {row['n_deals']} chollos. "
                 f"Revísalo y lánzalo con --send.", tags="newspaper")
            return 0

        if row["status"] == "draft" and not (args.auto_approve or args.send):
            ans = input("¿enviar a la lista gratuita? [s/N] ").strip().lower()
            if ans != "s":
                print("no enviado — queda como borrador")
                return 0

        # 2. Send under the ledger. The digest is approved only once the
        # client and the reservation are in hand — an aborted send must
        # leave an honest 'draft'/'degraded' trail, not a phantom
        # approval with a green ledger run.
        try:
            resend = ResendClient.from_env()
        except RuntimeError as exc:
            print(f"RESEND_API_KEY missing — cannot send ({exc})")
            return 2
        recipients: list[tuple[str, str | None]] = [
            (config.alert_email_to, None)]
        for r in conn.execute(
                "SELECT email, unsub_token FROM subscribers "
                "WHERE status = 'active' AND NOT EXISTS "
                "(SELECT 1 FROM suppressions x WHERE x.email = "
                "subscribers.email) ORDER BY email").fetchall():
            if r["email"] != config.alert_email_to:
                recipients.append((r["email"], r["unsub_token"]))

        ledger = QuotaLedger(conn)
        ledger.seed_pools()
        ledger.expire_orphans()
        run_id = ledger.begin_run(trigger=args.trigger, scope=SCOPE_VUELAZO)
        if run_id is None:
            print("another run holds the lease — try later")
            return 0
        status = "ok"
        try:
            _ensure_service_anchor(ledger, "resend")
            cost = CostVector(lines=(
                CostLine("resend", len(recipients), "primary", "digest"),))
            if not ledger.reserve(run_id, SEARCH_ID, cost):
                print("resend pool short — not sending")
                status = "degraded"
                from lib.pushes import push
                push("Vuelazo: digest NO enviado (pool short)",
                     "La reserva de resend fue rechazada — revisa la "
                     "cuota antes del próximo intento.",
                     priority="high", tags="warning")
                return 2
            dg.approve_digest(conn, digest_id)
            guarded = guard_clients({"resend": resend}, ledger=ledger,
                                    run_id=run_id, search_id=SEARCH_ID,
                                    shadow=False)
            subject = (f"Vuelazo — los {row['n_deals']} chollos de la "
                       f"semana")
            # Per-recipient dedup rides send_log(channel='digest',
            # deal_id=digest_id, provider_ref=<recipient>): a retry after
            # a mid-loop failure resumes instead of double-mailing the
            # already-sent half of the list.
            done = {r["provider_ref"] for r in conn.execute(
                "SELECT provider_ref FROM send_log WHERE channel = "
                "'digest' AND deal_id = ?", (digest_id,)).fetchall()}
            sent, failed = 0, 0
            for email, unsub_token in recipients:
                if email in done:
                    sent += 1
                    continue
                text = row["draft_es"]
                headers = None
                if unsub_token:
                    unsub = (f"{web_url}/api/subscribe/unsubscribe"
                             f"?token={unsub_token}")
                    text += f"\n\nDarse de baja: {unsub}"
                    headers = {
                        "List-Unsubscribe": f"<{unsub}>",
                        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                    }
                try:
                    guarded["resend"].send_email(
                        from_=config.email_from, to=email, subject=subject,
                        text=text, headers=headers)
                    deals_db.record_send(conn, channel="digest",
                                         deal_id=digest_id,
                                         provider_ref=email)
                    sent += 1
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    LOG.warning("digest send failed for %s: %s", email, exc)
            if failed == 0:
                dg.mark_digest_sent(conn, digest_id)
                print(f"digest sent to {sent} recipient(s)")
            else:
                status = "degraded"
                print(f"digest partially sent: {sent} ok, {failed} failed "
                      f"— re-run --send to resume (already-sent are "
                      f"skipped)")
        except Exception:  # noqa: BLE001
            LOG.exception("digest send failed")
            status = "failed"
        finally:
            ledger.settle(run_id, SEARCH_ID)
            ledger.finalize_run(run_id, status)
        return 0 if status == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
