#!/usr/bin/env python
"""Member lifecycle cron (M2, D5): lapse removal, T-30/T-7 renewal
reminders, refund-removal enforcement. Ledger-metered like every other
external call in this repo.

Usage: python scripts/member_lifecycle.py [--dry-run] [--json-summary f]

Env: TURSO_* (or local sqlite), TELEGRAM_BOT_TOKEN,
     TELEGRAM_PRIVATE_CHANNEL_ID (falls back to TELEGRAM_TEST_CHAT_ID),
     RESEND_API_KEY, WEB_URL (unsubscribe/renewal links).

Reminder copy is deliberately plain (D5: "sin renovación automática —
tú decides cada año"; founding price survives only on-time renewal).
Every reminder carries one-click List-Unsubscribe headers (RFC 8058) —
Gmail/Yahoo bulk rules, non-negotiable #7.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=REPO / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOG = logging.getLogger("members")

SEARCH_ID = "vuelazo-members"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _reminder_text(member, web_url: str) -> tuple[str, str]:
    """days-left is computed from member_until — the subject can never
    contradict reality (a late-firing batch says the TRUE days left)."""
    from datetime import datetime, timezone
    until = datetime.fromisoformat(
        member["member_until"].replace("Z", "+00:00"))
    days_left = max(0, (until - datetime.now(timezone.utc)).days)
    plan_note = ("Renovando a tiempo mantienes tu precio fundador."
                 if member["plan"] == "founding"
                 else "Renueva cuando quieras — sin renovación automática.")
    subject = (f"Tu pase Vuelazo caduca en {days_left} días"
               if days_left > 1 else "Tu pase Vuelazo caduca mañana")
    body = (
        f"Hola,\n\n"
        f"tu pase anual de Vuelazo caduca el {member['member_until'][:10]}.\n"
        f"En Vuelazo no hay renovación automática: tú decides cada año.\n"
        f"{plan_note}\n\n"
        f"Renovar: {web_url}/unete\n\n"
        f"Gracias por volar con nosotros,\nVuelazo — vuelazos desde tu "
        f"aeropuerto.")
    return subject, body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--trigger", default="local")
    ap.add_argument("--json-summary", default=None)
    args = ap.parse_args()

    from lib import db as db_mod
    from lib import deals_db, members_db
    from lib.clients import guard_clients
    from lib.dealconfig import load_deal_config
    from lib.planner import CostLine, CostVector
    from lib.quota import SCOPE_VUELAZO, QuotaLedger
    from lib.resend_api import ResendClient
    from lib.telegram_api import TelegramClient

    config = load_deal_config()
    # ${{ vars.WEB_URL }} with an unset repo var sets WEB_URL='' — treat
    # empty as unset or unsubscribe links go out relative (dead).
    web_url = (os.environ.get("WEB_URL", "").strip()
               or "https://flight-scans.vercel.app")
    channel = (os.environ.get("TELEGRAM_PRIVATE_CHANNEL_ID", "").strip()
               or os.environ.get("TELEGRAM_TEST_CHAT_ID", "").strip())
    summary: dict = {"lapsed": 0, "removed": 0, "reminded_t30": 0,
                     "reminded_t7": 0, "warnings": []}

    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        db_mod.ensure_schema(conn)
        deals_db.ensure_deals_schema(conn)
        members_db.ensure_members_schema(conn)

        ledger = QuotaLedger(conn)
        ledger.seed_pools()
        ledger.expire_orphans()
        run_id = ledger.begin_run(trigger=args.trigger, scope=SCOPE_VUELAZO)
        if run_id is None:
            print("another run holds the lease — exiting")
            return 0

        status = "ok"
        try:
            telegram = resend = None
            try:
                telegram = TelegramClient.from_env()
            except RuntimeError as exc:
                summary["warnings"].append(str(exc))
            try:
                resend = ResendClient.from_env()
            except RuntimeError as exc:
                summary["warnings"].append(str(exc))

            to_lapse = members_db.members_to_lapse(conn)
            # T-30 batch is floored at 7 days out: a late-firing run must
            # never claim '30 días' for a pass expiring in 5 (the T-7
            # stream owns that window).
            t30 = members_db.reminder_due(conn, days_ahead=30,
                                          min_days_ahead=7,
                                          event="reminded_t30")
            t7 = members_db.reminder_due(conn, days_ahead=7,
                                         event="reminded_t7")
            t7_ids = {m["id"] for m in t7}
            t30 = [m for m in t30 if m["id"] not in t7_ids]

            # 1. Flip expired members first (CAS) — removal is enforced
            #    below via the retryable needing-removal queue, exactly
            #    like refunds, so a failed Telegram call is retried on
            #    the next run instead of fire-and-forgotten.
            for m in to_lapse:
                if args.dry_run:
                    print(f"[dry] would lapse member {m['id']} {m['email']}")
                    continue
                if members_db.lapse_member(conn, m["id"]):
                    summary["lapsed"] += 1
                    print(f"lapsed member {m['id']}")

            need_removal = ([] if args.dry_run
                            else members_db.members_needing_removal(conn))

            lines = []
            if telegram and channel and need_removal:
                # remove_member is metered at 2 units (ban + unban).
                lines.append(CostLine("telegram", 2 * len(need_removal),
                                      "primary", "membership removals"))
            if resend and (t30 or t7):
                lines.append(CostLine("resend", len(t30) + len(t7),
                                      "primary", "renewal reminders"))
            from run_deals import _ensure_service_anchor
            _ensure_service_anchor(ledger, "resend")
            if lines and not args.dry_run:
                if not ledger.reserve(run_id, SEARCH_ID,
                                      CostVector(lines=tuple(lines))):
                    print("pool short — aborting")
                    ledger.finalize_run(run_id, "degraded")
                    return 2
            guarded = guard_clients(
                {"telegram": telegram, "resend": resend}, ledger=ledger,
                run_id=run_id, search_id=SEARCH_ID, shadow=False)

            # 2. Channel removals (lapsed + refunded), per-member
            #    isolated: one dead Telegram account must not block the
            #    rest of the queue or the reminders below.
            from lib.telegram_api import TelegramError
            for m in need_removal:
                if guarded["telegram"] is None or not channel:
                    break
                try:
                    guarded["telegram"].remove_member(
                        chat_id=channel, user_id=m["telegram_user_id"])
                except TelegramError as exc:
                    if 400 <= (exc.status_code or 0) < 500:
                        # Permanent (deleted account, bad id): drain the
                        # queue anyway — there is nobody left to remove.
                        LOG.warning("removal 4xx for member %d (%s) — "
                                    "clearing binding", m["id"], exc)
                    else:
                        summary["warnings"].append(
                            f"removal failed member {m['id']}: {exc}")
                        continue  # transient: retried next run
                conn.execute("UPDATE members SET telegram_user_id = NULL "
                             "WHERE id = ?", (m["id"],))
                members_db.log_event(conn, m["id"], "tg_removed",
                                     m["status"])
                summary["removed"] += 1

            # 3. Renewal reminders (suppressions honored; List-Unsubscribe
            #    one-click headers on every bulk send). Per-member
            #    isolation: one bounced address never starves the rest.
            for batch, event in ((t30, "reminded_t30"), (t7, "reminded_t7")):
                for m in batch:
                    if deals_db.is_suppressed(conn, m["email"]):
                        continue
                    if args.dry_run or guarded["resend"] is None:
                        print(f"[dry] would send {event}: {m['email']}")
                        continue
                    try:
                        token = members_db.mint_token(conn, m["id"],
                                                      purpose="unsub")
                        unsub = f"{web_url}/api/unsubscribe?token={token}"
                        subject, body = _reminder_text(m, web_url)
                        guarded["resend"].send_email(
                            from_=config.email_from, to=m["email"],
                            subject=subject,
                            text=body + f"\n\nDarse de baja: {unsub}",
                            headers={
                                "List-Unsubscribe": f"<{unsub}>",
                                "List-Unsubscribe-Post":
                                    "List-Unsubscribe=One-Click",
                            })
                        members_db.log_event(conn, m["id"], event)
                        summary[event] += 1
                    except Exception as exc:  # noqa: BLE001
                        summary["warnings"].append(
                            f"{event} failed for member {m['id']}: {exc}")

        except Exception:  # noqa: BLE001
            LOG.exception("member lifecycle failed")
            status = "failed"
        finally:
            ledger.settle(run_id, SEARCH_ID)
            ledger.finalize_run(run_id, status)

        print(json.dumps(summary))
        if args.json_summary:
            Path(args.json_summary).write_text(json.dumps(summary, indent=2),
                                               encoding="utf-8")
        return 0 if status == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
