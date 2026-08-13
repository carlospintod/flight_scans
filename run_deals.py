"""Vuelazo M0 walking skeleton — one command, the whole chain.

VLC origin-only sweep (aviasales cached, free) -> D2 day-one gate ->
ONE SerpAPI live verification -> cross-route score -> Claude draft ->
console approve -> private test Telegram post + email to Carlos.

Every metered call is reserved BEFORE it happens and settled after —
the run prints the reserved-vs-used receipt (predicted = upper bound).

Usage:
    python run_deals.py [--trigger local] [--auto-approve]
                        [--skip-publish] [--json-summary out.json]

Missing service keys degrade the chain, never crash it: the run stops at
the step whose key is absent, says exactly which env var to set, and
everything before it stays persisted (a re-run picks up cleanly).

Exit codes: 0 ran; 1 fatal (DB/lease); 2 degraded (unexpected error).
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
LOG = logging.getLogger("deals")

EXIT_OK, EXIT_FATAL, EXIT_DEGRADED = 0, 1, 2
SEARCH_ID = "vuelazo-deals"

# Vuelazo spends from ITS OWN ledger pools. The NBO tracker (run_batch)
# keeps `aviasales`/`serpapi`; these `_vz` ids are the same adapters
# against Vuelazo's own budget, so a deal sweep can never drain the
# tracker's free tier. See lib/sources.py for the pool semantics.
SRC_CACHED = "aviasales_vz"     # discovery (Travelpayouts cached)
SRC_GOOGLE = "serpapi_vz"       # insights + second opinion (paid, 50/mo slice)
SRC_SCRAPER = "googleflights_vz"  # verification (free Playwright scraper)
FARE_SOURCES = [SRC_CACHED, SRC_GOOGLE, SRC_SCRAPER]
REJECT_REASONS = ("too_common", "bad_dates", "ulcc_junk", "thin_saving", "other")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _ensure_service_anchor(ledger, source: str) -> None:
    """Self-imposed monthly budgets (anthropic, resend) have no provider
    counter. Seed the baseline anchor at period_limit, and RE-anchor at
    the turn of each month — these are OUR OWN budgets, so presuming our
    own monthly reset is correct (unlike provider pools, which are never
    presumed). Without this, an exhausted self-pool would refuse every
    reservation forever and silently halt the whole pipeline."""
    from lib.sources import POOL_SEEDS
    state = ledger.pool_state(source)
    if state is None or state.pool_kind != "monthly":
        return
    limit = next((s[2] for s in POOL_SEEDS if s[0] == source), None)
    if not limit:
        return
    month = _now_iso()[:7]
    if state.provider_view is None:
        ledger.record_anchor(source, remaining=limit, limit_total=limit,
                             origin="seed")
        LOG.info("ledger: seeded %s self-budget anchor at %d", source, limit)
    elif (state.baseline_at or "")[:7] < month:
        ledger.record_anchor(source, remaining=limit, limit_total=limit,
                             origin="self_monthly_reset")
        LOG.info("ledger: %s self-budget re-anchored at %d for %s",
                 source, limit, month)


def _ensure_fare_anchor(ledger, source: str, client) -> None:
    """Anchor a Vuelazo fare pool, by whichever rule is HONEST for it.

    Dedicated key  -> probe that account's own counter (/account is
                      unmetered), exactly as run_batch does.
    Shared key     -> self-imposed slice. The provider counter reports
                      the WHOLE account, so anchoring both projects'
                      pools from it would let each believe it owns the
                      full allowance — the precise way a Vuelazo sweep
                      would silently eat the tracker's free 250.
    """
    import os

    from lib.sources import shares_key_with_other_project

    state = ledger.pool_state(source)
    if state is None or state.pool_kind != "monthly":
        return
    if shares_key_with_other_project(source, os.environ):
        _ensure_service_anchor(ledger, source)
        return
    # A dedicated key arrived. Any baseline we invented for ourselves
    # while borrowing (origin 'seed'/'self_monthly_reset') is now a
    # fiction that would cap the new account at the old slice — a real
    # provider counter always outranks a self-imposed one.
    self_imposed = state.baseline_origin in ("seed", "self_monthly_reset")
    if state.provider_view is not None and not self_imposed:
        return
    if client is None:
        return
    probe = getattr(client, "check_quota", None)
    if probe is None:
        _ensure_service_anchor(ledger, source)
        return
    try:
        q = probe()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("%s /account probe failed: %s", source, exc)
        return
    if isinstance(q.get("remaining"), int):
        ledger.record_anchor(source, remaining=q["remaining"],
                             limit_total=q.get("limit_total"),
                             origin="account_api")
        LOG.info("ledger: anchored %s at %s from /account",
                 source, q["remaining"])


def _print_card(deal_id, cand, verify, draft_text, confidence) -> None:
    line = "─" * 62
    print(f"\n{line}")
    print(f" DEAL #{deal_id}  {cand.origin} → {cand.dest}"
          f"   {verify.live_price or cand.price} {cand.currency}"
          f"   [{cand.deal_class}]  score {cand.score}")
    print(f"  fechas  {cand.depart_date} → {cand.return_date or 'solo ida'}")
    print(f"  clase   {cand.route_class}  ·  mediana clase "
          f"{cand.xsection_median} {cand.currency}  ·  −{cand.pct_below:.0f}%"
          f"  ·  ahorro {cand.abs_saving} {cand.currency}")
    print(f"  verif   {verify.note}  ·  carrier {verify.carriers or '—'}")
    print(f"  conf    {confidence.level} ~{confidence.score}%"
          f"  ({', '.join(confidence.families)})")
    print(f"{line}\n{draft_text}\n{line}")


def _console_approve(auto: bool) -> tuple[str, str | None, str | None]:
    """-> (action, reject_reason, edited_text). Actions: approve|reject|skip."""
    if auto:
        print("  --auto-approve: aprobado sin consola")
        return "approve", None, None
    while True:
        ans = input("[a]probar / [r]echazar / [e]ditar y aprobar / "
                    "[s]altar > ").strip().lower()
        if ans == "a":
            return "approve", None, None
        if ans == "s":
            return "skip", None, None
        if ans == "r":
            print("  motivo: " + " | ".join(
                f"{i+1}={r}" for i, r in enumerate(REJECT_REASONS)))
            pick = input("  motivo (1-5) > ").strip()
            try:
                reason = REJECT_REASONS[int(pick) - 1]
            except (ValueError, IndexError):
                reason = "other"
            return "reject", reason, None
        if ans == "e":
            print("  pega el texto nuevo; termina con una línea con solo '.'")
            lines: list[str] = []
            while True:
                row = input()
                if row.strip() == ".":
                    break
                lines.append(row)
            edited = "\n".join(lines).strip()
            if edited:
                return "approve", None, edited
            print("  (vacío — ignorado)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Vuelazo deal run")
    ap.add_argument("--trigger", default="local")
    ap.add_argument("--queue", action="store_true",
                    help="cron mode: no console — drafts land in the queue "
                         "('queued'); already-approved deals are published")
    ap.add_argument("--auto-approve", action="store_true",
                    help="skip the console approval (demo runs)")
    ap.add_argument("--skip-publish", action="store_true",
                    help="stop after approval; no Telegram/email")
    ap.add_argument("--publish-only", action="store_true",
                    help="only publish already-approved deals, then exit "
                         "(the /ops approve button dispatches this)")
    ap.add_argument("--json-summary", default=None)
    args = ap.parse_args()

    from lib import db as db_mod
    from lib import deals_db, members_db
    from lib.clients import guard_clients, make_clients
    from lib.credentials import load_credentials_into_env
    from lib.dealconfig import load_deal_config
    from lib.dealgate import classify_route, gate_candidates
    from lib import dealpipe
    from lib.planner import CostLine, CostVector
    from lib.quota import SCOPE_VUELAZO, QuotaExceeded, QuotaLedger

    config = load_deal_config()
    summary: dict = {"started_at": _now_iso(), "steps": {}, "warnings": []}

    from lib import digest as dg

    with db_mod.connect(REPO / "data" / "tracker.db") as conn:
        db_mod.ensure_schema(conn)
        deals_db.ensure_deals_schema(conn)
        members_db.ensure_members_schema(conn)
        dg.ensure_digest_schema(conn)
        load_credentials_into_env(conn)

        ledger = QuotaLedger(conn)
        ledger.seed_pools()
        ledger.expire_orphans()
        run_id = ledger.begin_run(trigger=args.trigger, scope=SCOPE_VUELAZO)
        if run_id is None and args.publish_only:
            # The /ops approve dispatch expects fan-out within minutes;
            # a scan holding the lease is normal — wait it out briefly.
            import time
            for _ in range(10):
                time.sleep(30)
                ledger.expire_orphans()
                run_id = ledger.begin_run(trigger=args.trigger, scope=SCOPE_VUELAZO)
                if run_id:
                    break
        if run_id is None:
            print("another run holds the lease — try again in a few minutes")
            if args.trigger != "local":
                from lib.pushes import push
                push("Vuelazo: run saltado (lease ocupado)",
                     f"El run '{args.trigger}' no pudo ejecutar — otro run "
                     f"tiene el lease. Se reintenta en el próximo cron.",
                     tags="warning")
            return EXIT_OK
        summary["run_id"] = run_id

        status = "ok"
        try:
            # ---- clients (fare rails via the shared path; service rails
            #      constructed here — their keys are env-only secrets) ----
            raw, warnings = make_clients(FARE_SOURCES, conn)
            for w in warnings:
                LOG.warning("%s", w)
                summary["warnings"].append(w)

            # A rail the CONFIG switches on but that cannot be built is a
            # silent outage, not a quiet day. Measured 2026-08-10: the
            # Explore rail was enabled in vuelazo.yaml, its key was
            # missing from the Actions secrets, and every scheduled run
            # skipped discovery's paid half without a word — for a full
            # day. Anything landing in `dead_rails` gets pushed at the
            # end of the run (never-silent principle, same as the
            # pool-short page).
            dead_rails: list[str] = []

            def _try_service(name, build, *, required: bool = False):
                try:
                    return build(), None
                except RuntimeError as exc:
                    summary["warnings"].append(f"{name}: {exc}")
                    if required:
                        dead_rails.append(f"{name}: {exc}")
                    return None, str(exc)

            from lib.drafting import AnthropicDraftClient
            from lib.resend_api import ResendClient
            from lib.telegram_api import TelegramClient, chat_id_from_env

            drafter, drafter_err = _try_service(
                "anthropic", lambda: AnthropicDraftClient.from_env(
                    model=config.draft_model,
                    max_tokens=config.draft_max_tokens),
                required=True)      # no drafting, no publishable deal
            telegram, telegram_err = _try_service(
                "telegram", TelegramClient.from_env,
                required="tg_private" in config.publish_channels)
            # Members are admitted into the PRIVATE channel (M2); the
            # test channel is the pre-launch fallback.
            tg_chat: str | None = os.environ.get(
                "TELEGRAM_PRIVATE_CHANNEL_ID", "").strip() or None
            if tg_chat is None:
                try:
                    tg_chat = chat_id_from_env()
                except RuntimeError as exc:
                    if telegram is not None:
                        telegram, telegram_err = None, str(exc)
                        summary["warnings"].append(f"telegram: {exc}")
            resend, resend_err = _try_service(
                "resend", ResendClient.from_env,
                required="email" in config.publish_channels)

            _ensure_fare_anchor(ledger, SRC_GOOGLE, raw.get(SRC_GOOGLE))
            _ensure_service_anchor(ledger, "anthropic")
            _ensure_service_anchor(ledger, "resend")

            # ---- reserve the run's worst case, source by source ----
            months = dealpipe.sweep_months(date.today(),
                                           config.sweep_months_ahead)
            n_cand = 0 if args.publish_only else config.max_candidates_per_run

            # Watchlist refresh is 1x/day (D1): skip only when today's
            # refresh actually PRODUCED something (result ok/empty above
            # a floor) — a run that died three calls in, or a full
            # aviasales outage (all charged, all failed), must not eat
            # the day's coverage of a free source.
            today_prefix = _now_iso()[:10]
            all_wl = dealpipe.watchlist_routes(config)
            expected_wl = max(1, sum(m for _, _, m in all_wl))
            refreshed_today = conn.execute(
                "SELECT COUNT(*) FROM spend_events WHERE source=? "
                "AND op='prices_for_dates' AND spent_at LIKE ? "
                "AND result IN ('ok', 'empty')",
                (SRC_CACHED, today_prefix + "%")
            ).fetchone()[0] >= expected_wl // 4 + 1
            wl_routes = ([] if (args.publish_only or refreshed_today)
                         else all_wl)

            # Explore rotation for today. Once a day like the watchlist:
            # the grid is deterministic per date, so three cron runs
            # would otherwise sweep the SAME windows three times.
            explore_windows: list[tuple[str, str, str]] = []
            explore_raw = None
            if config.explore_enabled and not args.publish_only:
                done_today = conn.execute(
                    "SELECT COUNT(*) FROM spend_events WHERE source=? "
                    "AND op='explore' AND spent_at LIKE ? "
                    "AND result IN ('ok', 'empty')",
                    (SRC_GOOGLE, today_prefix + "%")).fetchone()[0]
                if done_today < config.explore_calls_per_day:
                    from lib.explore_api import ExploreClient, rotation_plan
                    explore_windows = rotation_plan(
                        list(config.origins), list(config.explore_areas),
                        months, day=date.today(),
                        budget=config.explore_calls_per_day - done_today)
                    explore_raw, explore_err = _try_service(
                        "explore",
                        lambda: ExploreClient.from_env(
                            provider=config.explore_provider),
                        required=True)   # config says ON: never skip quietly
                    if explore_raw is None:
                        explore_windows = []

            # Only deals WITH a draft can fan out; a draftless approved
            # row would otherwise inflate every reservation forever
            # while never publishing.
            pending_publish = [] if args.skip_publish else [
                dict(r) for r in conn.execute(
                    "SELECT * FROM deals WHERE status = 'approved' "
                    "AND draft_es IS NOT NULL AND draft_es != '' "
                    "ORDER BY approved_at").fetchall()]
            stuck_approved = conn.execute(
                "SELECT COUNT(*) FROM deals WHERE status = 'approved' "
                "AND (draft_es IS NULL OR draft_es = '')").fetchone()[0]
            if stuck_approved:
                LOG.warning("%d approved deal(s) have no draft — excluded "
                            "from publish; fix or reject them in /ops",
                            stuck_approved)
            n_pub = n_cand + len(pending_publish)
            # Audience SNAPSHOT at reserve time: the fan-out iterates
            # exactly these lists, so a member signing up mid-run (the
            # Stripe webhook writes concurrently) can never push actual
            # sends past the reserved upper bound.
            audience_by_origin = {
                o: members_db.active_members_for_origin(conn, o)
                for o in config.origins}
            n_members = max((len(v) for v in audience_by_origin.values()),
                            default=0)

            lines = []
            if not args.publish_only:
                # sweep: origins x months x sortings; watchlist: one call
                # per (route, month) of coverage.
                sweep_calls = (len(months) * len(config.origins)
                               * max(1, len(config.sweep_sortings)))
                wl_calls = sum(m for _, _, m in wl_routes)
                lines.append(CostLine(
                    SRC_CACHED, sweep_calls + wl_calls,
                    "primary", "anywhere sweep + watchlist refresh"))
                if raw.get(SRC_SCRAPER):
                    lines.append(CostLine(SRC_SCRAPER, n_cand, "primary",
                                          "live verification (free)"))
                if raw.get(SRC_GOOGLE) or explore_windows:
                    # ONE pool pays for both jobs: verification of
                    # survivors and today's Explore rotation.
                    n_verify = n_cand if raw.get(SRC_GOOGLE) else 0
                    lines.append(CostLine(
                        SRC_GOOGLE, n_verify + len(explore_windows), "primary",
                        f"insights + second opinion (<={n_verify}), "
                        f"explore rotation ({len(explore_windows)})"))
                if drafter:
                    lines.append(CostLine("anthropic", n_cand, "primary",
                                          "draft"))
            # T+24h free-channel fan-out (M3): free picks whose member
            # exclusivity expired and that haven't hit the public channel.
            free_due = ([] if args.skip_publish
                        else dg.free_picks_due(conn))
            tg_public = os.environ.get("TELEGRAM_PUBLIC_CHANNEL_ID",
                                       "").strip()
            tg_on = "tg_private" in config.publish_channels
            if (telegram and tg_on and not args.skip_publish
                    and (n_pub or free_due)):
                lines.append(CostLine("telegram", n_pub + len(free_due),
                                      "primary", "posts (private + public)"))
            if ("email" in config.publish_channels and resend
                    and not args.skip_publish and n_pub):
                lines.append(CostLine("resend", n_pub * (1 + n_members),
                                      "primary", "owner + member emails"))
            if not lines:
                print("nothing to do (publish-only with no approved deals?)")
                ledger.finalize_run(run_id, "ok")
                return EXIT_OK
            cost = CostVector(lines=tuple(lines))
            if not ledger.reserve(run_id, SEARCH_ID, cost):
                print("pool short — a quota pool refused the reservation; "
                      "see run_reservations for which")
                # A silently-refused SCHEDULED run is an outage, not a
                # quiet day — page it (never-silent principle).
                if args.trigger != "local":
                    from lib.pushes import push
                    push("Vuelazo: reserva rechazada (pool short)",
                         "Un pool de cuota rechazó la reserva del run — "
                         "mira run_reservations en /ops.",
                         priority="high", tags="warning")
                ledger.finalize_run(run_id, "degraded")
                return EXIT_DEGRADED
            print("reserved (upper bound): " + ", ".join(
                f"{ln.source}≤{ln.units}" for ln in lines))

            guarded = guard_clients(
                {SRC_CACHED: raw.get(SRC_CACHED),
                 SRC_GOOGLE: raw.get(SRC_GOOGLE),
                 SRC_SCRAPER: raw.get(SRC_SCRAPER),
                 "anthropic": drafter, "telegram": telegram, "resend": resend},
                ledger=ledger, run_id=run_id, search_id=SEARCH_ID,
                shadow=False)

            # serpapi_vz funds TWO jobs. GuardedClient counts down its
            # OWN budget, so two proxies on one source would each be
            # handed the whole reservation and could together exceed it
            # — breaking "predicted = guaranteed upper bound". Split the
            # reservation explicitly instead.
            if explore_raw is not None and explore_windows:
                from lib.quota import GuardedClient
                if guarded.get(SRC_GOOGLE) is not None:
                    guarded[SRC_GOOGLE] = GuardedClient(
                        raw[SRC_GOOGLE], ledger=ledger, source=SRC_GOOGLE,
                        run_id=run_id, search_id=SEARCH_ID, shadow=False,
                        budget_units=n_cand)
                guarded["explore"] = GuardedClient(
                    explore_raw, ledger=ledger, source=SRC_GOOGLE,
                    run_id=run_id, search_id=SEARCH_ID, shadow=False,
                    budget_units=len(explore_windows))

            def _publish(deal_id: int, origin: str, dest: str, price,
                         currency: str, text: str) -> list[str]:
                """Fan-out one approved deal; returns the targets hit.

                IDEMPOTENT: send_log is consulted before every send (the
                'audit + dedup base' doing its job) — a partial failure
                re-run never double-posts the channel or double-mails a
                member. Per-recipient isolation: one bad address logs a
                warning instead of aborting the whole fan-out."""
                already = conn.execute(
                    "SELECT channel, member_id FROM send_log "
                    "WHERE deal_id = ?", (deal_id,)).fetchall()
                sent_channels = {r["channel"] for r in already
                                 if r["member_id"] is None}
                sent_members_ids = {r["member_id"] for r in already
                                    if r["member_id"] is not None}
                targets: list[str] = []
                # ntfy: the same free phone-push rail the NBO tracker
                # uses. Unmetered, so outside the ledger (consistent with
                # scripts/notify_ntfy.py); send_log still dedups it.
                if "ntfy" in config.publish_channels:
                    if "ntfy" in sent_channels:
                        targets.append("ntfy")
                    else:
                        from lib.pushes import push
                        if push(f"Vuelazo: {origin}→{dest} {price} {currency}",
                                text, priority="high", tags="airplane"):
                            deals_db.record_send(conn, channel="ntfy",
                                                 deal_id=deal_id,
                                                 provider_ref="ntfy")
                            targets.append("ntfy")
                            print(f"  ntfy: enviado al móvil (deal #{deal_id})")
                        else:
                            print("  ntfy no configurado (NTFY_TOPIC) — "
                                  "sin push")
                if ("tg_private" in config.publish_channels
                        and guarded["telegram"] is not None and tg_chat):
                    if "tg_private" in sent_channels:
                        targets.append("tg_private")
                    else:
                        sent = guarded["telegram"].send_message(
                            chat_id=tg_chat, text=text)
                        deals_db.record_send(conn, channel="tg_private",
                                             deal_id=deal_id,
                                             provider_ref=str(sent.message_id))
                        targets.append("tg_private")
                        print(f"  telegram: publicado deal #{deal_id} "
                              f"(message_id {sent.message_id})")
                if ("email" in config.publish_channels
                        and guarded["resend"] is not None):
                    subject = (f"Vuelazo: {origin}→{dest} por "
                               f"{price} {currency}")
                    web_url = (os.environ.get("WEB_URL", "").strip()
                               or "https://flight-scans.vercel.app")
                    if "email" in sent_channels:
                        targets.append("email")
                    elif deals_db.is_suppressed(conn, config.alert_email_to):
                        print("  email suprimido — destinatario en la lista "
                              "de supresión")
                    else:
                        mail = guarded["resend"].send_email(
                            from_=config.email_from,
                            to=config.alert_email_to,
                            subject=subject, text=text)
                        deals_db.record_send(conn, channel="email",
                                             deal_id=deal_id,
                                             provider_ref=mail.email_id)
                        targets.append("email")
                        print(f"  email: enviado a {config.alert_email_to} "
                              f"(id {mail.email_id})")
                    # Member fan-out with per-airport filtering (M2, D4)
                    # over the reserve-time SNAPSHOT. Every member alert
                    # is bulk mail: List-Unsubscribe one-click headers +
                    # suppression check per address.
                    audience = audience_by_origin.get(origin, [])
                    sent_members = 0
                    for m in audience:
                        if m["id"] in sent_members_ids:
                            sent_members += 1
                            continue
                        if m["email"] == config.alert_email_to:
                            continue
                        if deals_db.is_suppressed(conn, m["email"]):
                            continue
                        try:
                            unsub_token = members_db.mint_token(
                                conn, m["id"], purpose="unsub")
                            unsub = (f"{web_url}/api/unsubscribe"
                                     f"?token={unsub_token}")
                            mail = guarded["resend"].send_email(
                                from_=config.email_from, to=m["email"],
                                subject=subject,
                                text=text + f"\n\nDarse de baja: {unsub}",
                                headers={
                                    "List-Unsubscribe": f"<{unsub}>",
                                    "List-Unsubscribe-Post":
                                        "List-Unsubscribe=One-Click",
                                })
                            deals_db.record_send(conn, channel="email",
                                                 deal_id=deal_id,
                                                 member_id=m["id"],
                                                 provider_ref=mail.email_id)
                            sent_members += 1
                        except QuotaExceeded:
                            raise  # budget guard: never absorb
                        except Exception as exc:  # noqa: BLE001
                            LOG.warning("member send failed (deal %d, "
                                        "member %d): %s", deal_id,
                                        m["id"], exc)
                            summary["warnings"].append(
                                f"member send failed: deal {deal_id} "
                                f"member {m['id']}: {exc}")
                    if audience:
                        print(f"  email: {sent_members}/{len(audience)} "
                              f"miembro(s) con {origin} en sus aeropuertos")
                if targets:
                    deals_db.update_deal(conn, deal_id, status="published",
                                         published_at=_now_iso(),
                                         publish_targets=json.dumps(targets))
                return targets

            # ---- 0a. T+24h free picks -> public channel (M3, D4) ----
            for row in free_due:
                if guarded["telegram"] is None or not tg_public:
                    if free_due:
                        print(f"free picks due ({len(free_due)}) but "
                              f"TELEGRAM_PUBLIC_CHANNEL_ID/bot missing")
                    break
                sent = guarded["telegram"].send_message(
                    chat_id=tg_public,
                    text=(row["draft_es"] or "") +
                         "\n\n⏱ Los miembros recibieron este vuelazo hace "
                         "24 horas. Todos, al instante: vuelazo.es/unete")
                deals_db.record_send(conn, channel="tg_public",
                                     deal_id=row["id"],
                                     provider_ref=str(sent.message_id))
                print(f"  free pick #{row['id']} → canal público "
                      f"(T+24h, message {sent.message_id})")

            # ---- 0. publish deals approved since the last run ----
            published_now = 0
            for row in pending_publish:
                if not row.get("draft_es"):
                    continue
                got = _publish(row["id"], row["origin"], row["dest"],
                               row["price"], row["currency"], row["draft_es"])
                published_now += 1 if got else 0
            if pending_publish:
                print(f"publish: {published_now}/{len(pending_publish)} "
                      f"approved deal(s) fanned out")
                summary["steps"]["publish_pending"] = published_now
            if args.publish_only:
                _write_summary(args, summary)
                return EXIT_OK

            # ---- 1. discover ----
            if guarded[SRC_CACHED] is None:
                print("TRAVELPAYOUTS_TOKEN missing — cannot discover. Stop.")
                ledger.finalize_run(run_id, "degraded")
                return EXIT_DEGRADED
            obs: list[deals_db.Observation] = []
            for origin in config.origins:
                obs.extend(dealpipe.sweep_origin(
                    guarded[SRC_CACHED], origin=origin, months=months,
                    currency=config.currency,
                    sortings=config.sweep_sortings,
                    limits=config.sweep_limits))
            if wl_routes:
                wl_obs = dealpipe.watchlist_refresh(
                    guarded[SRC_CACHED], routes=wl_routes,
                    currency=config.currency)
                print(f"watchlist: {len(wl_obs)} observations from "
                      f"{len(wl_routes)} route refreshes")
                obs.extend(wl_obs)

            # Google Travel Explore: the long-haul rail. Same corpus we
            # verify against, real airport codes, real date pairs — so
            # unlike the cache it nominates candidates that can actually
            # be confirmed. Degrades per window, never aborts discovery.
            n_explore = 0
            explore_failed = 0
            explore_last_error = ""
            explore_persisted: list[deals_db.Observation] = []
            if explore_windows and guarded.get("explore") is not None:
                from lib.explore_api import ExploreError, to_observations
                for eo, area, emonth in explore_windows:
                    try:
                        quotes = guarded["explore"].explore(
                            origin=eo, month=emonth, area=area,
                            currency=config.currency)
                    except (ExploreError, QuotaExceeded) as exc:
                        LOG.warning("explore %s/%s/%s failed: %s",
                                    eo, area, emonth, exc)
                        explore_failed += 1
                        explore_last_error = f"{area}/{emonth}: {exc}"
                        continue
                    # Persist per window, not at the end of discovery.
                    # Explore is the ONLY metered discovery call: the
                    # free sweep can afford to be lost on a crash, four
                    # paid credits cannot. (Measured: a run died between
                    # the Explore loop and the bulk insert and threw
                    # away 4 charged calls' worth of rows.)
                    window_obs = to_observations(quotes)
                    try:
                        deals_db.insert_observations(conn, window_obs)
                    except Exception as exc:  # noqa: BLE001
                        LOG.warning("explore %s/%s/%s: rows not persisted "
                                    "(%s) — keeping them for this run",
                                    eo, area, emonth, exc)
                        obs.extend(window_obs)
                    else:
                        explore_persisted.extend(window_obs)
                    n_explore += len(quotes)
                print(f"explore: {n_explore} observations from "
                      f"{len(explore_windows)} window(s) "
                      f"({', '.join(f'{o}/{a}/{m}' for o, a, m in explore_windows)})")
                # A rail that BUILDS but returns nothing all run is the
                # failure the dead_rails check cannot see. Measured
                # 2026-08-11: a wrong area kgmid errored 8/8 calls
                # across two runs, burned the credits, and the runs
                # still reported 'ok'. Empty windows are legitimate
                # (asia/October really is empty from MAD) — but EVERY
                # window empty means the rail, not the market.
                if n_explore == 0 and explore_failed == len(explore_windows):
                    dead_rails.append(
                        f"explore: {len(explore_windows)}/"
                        f"{len(explore_windows)} calls failed "
                        f"({explore_last_error})")
                summary["steps"]["explore"] = {
                    "windows": [list(w) for w in explore_windows],
                    "observations": n_explore}

            deals_db.insert_observations(conn, obs)
            # Explore rows are already persisted (per window, above);
            # they still belong in this run's gate input and reach.
            obs = obs + explore_persisted

            # Long-haul reach is THE product metric — surface it every run.
            by_class: dict[str, set] = {}
            for o in obs:
                by_class.setdefault(
                    classify_route(o.dest, config), set()).add((o.origin, o.dest))
            reach = {k: len(v) for k, v in sorted(by_class.items())}
            print(f"reach by class: {reach}")
            summary["steps"]["reach"] = reach
            for origin, dest in sorted({(o.origin, o.dest) for o in obs}):
                deals_db.touch_watchlist(
                    conn, origin=origin, dest=dest,
                    route_class=classify_route(dest, config))
            print(f"discover: {len(obs)} observations across "
                  f"{len({(o.origin, o.dest) for o in obs})} routes "
                  f"({n_explore} from explore)")
            summary["steps"]["discover"] = {"observations": len(obs),
                                            "explore": n_explore}

            # ---- 2. gate ----
            cands = gate_candidates(conn, obs, config)
            survivors = [c for c in cands if c.rejected_reason is None]
            killed = [c for c in cands if c.rejected_reason]
            for c in killed:
                print(f"gate: {c.origin}->{c.dest} {c.price} "
                      f"{c.currency} killed by {c.rejected_reason}")
            print(f"gate: {len(survivors)} candidate(s) of {len(cands)} "
                  f"gate-passers survive the guardrails")
            summary["steps"]["gate"] = {
                "passed": len(cands), "survived": len(survivors),
                "killed": {c.rejected_reason: f"{c.origin}->{c.dest}"
                           for c in killed}}
            if not survivors:
                print("no candidate today — the chain stops here (normal).")
                # settle/receipt happen in `finally`
                _write_summary(args, summary)
                return EXIT_OK

            # ---- per-candidate: verify -> draft -> approve -> publish ----
            queued_deals: list[tuple] = []
            for cand in survivors:
                # Interactive approval can outlive the 20-min lease; keep
                # it alive so no concurrent run starts mid-spend.
                ledger.heartbeat(run_id)
                deal_id = deals_db.insert_deal(
                    conn, origin=cand.origin, dest=cand.dest,
                    sample_dates=f"{cand.depart_date}"
                                 f"..{cand.return_date or ''}",
                    price=cand.price, currency=cand.currency,
                    pct_below=cand.pct_below, abs_saving=cand.abs_saving,
                    score=cand.score,
                    **{"class": cand.deal_class})
                summary.setdefault("deals", []).append(deal_id)

                # 3. verify (no alert without live verification).
                # Two stages since 2026-08-09: the FREE scraper proves
                # the fare is real; serpapi (the paid 50/mo slice) runs
                # only on survivors, adding the typical range the
                # enforced floor needs plus a third price opinion.
                # Scraper down (captcha/CI) -> serpapi alone, as before.
                if guarded[SRC_GOOGLE] is None and guarded[SRC_SCRAPER] is None:
                    print("no verification rail (SERPAPI_KEY missing and "
                          "no local browser) — cannot verify. Stop.")
                    deals_db.update_deal(conn, deal_id, status="expired")
                    status = "degraded"
                    break
                stage1_src = (SRC_SCRAPER if guarded[SRC_SCRAPER] is not None
                              else SRC_GOOGLE)
                verify = dealpipe.verify_candidate(guarded[stage1_src], cand,
                                                   config)
                stage2 = (verify.ok and stage1_src == SRC_SCRAPER
                          and guarded[SRC_GOOGLE] is not None)
                if stage2:
                    verify = dealpipe.second_opinion(
                        guarded[SRC_GOOGLE], cand, verify, config)
                confidence = dealpipe.deal_confidence(
                    cached_produced=True, live_verified=verify.ok)
                refs = {"cached_price": cand.price,
                        "live_price": verify.live_price,
                        "source": (f"{stage1_src}+{SRC_GOOGLE}" if stage2
                                   else stage1_src),
                        "note": verify.note,
                        # Which airport the price was actually proven at
                        # (NYC nominates, JFK proves).
                        "verified_airport": verify.airport,
                        "checked_at": _now_iso()}
                if not verify.ok:
                    deals_db.update_deal(
                        conn, deal_id, status="expired",
                        verification_refs=json.dumps(refs),
                        confidence=json.dumps(confidence.as_dict()))
                    # The cache said one thing, the live market another.
                    # It will still say it on the next run three hours
                    # from now, so stop asking for a day.
                    if verify.live_price is not None:
                        deals_db.record_disproved(
                            conn, origin=cand.origin, dest=cand.dest,
                            depart_date=cand.depart_date,
                            return_date=cand.return_date,
                            cached=cand.price, live=verify.live_price,
                            hours=config.disproved_cooldown_hours)
                    print(f"verify: {cand.origin}->{cand.dest} died quietly "
                          f"({verify.note})")
                    continue
                from dataclasses import replace as _replace
                cand = _replace(cand, deal_class=dealpipe.classify_deal(
                    cand, verify, config))
                deals_db.update_deal(conn, deal_id,
                                     **{"class": cand.deal_class})

                # THE gate (2026-08-13): the live price against this
                # itinerary's OWN 60-day history, which every
                # verification already returns. Recorded whether or not
                # it is enforced, so its effect stays measurable.
                refs["history"] = verify.history
                hv = verify.history or {}
                if config.history_gate and hv.get("level") not in (None, "unknown"):
                    if not hv.get("is_deal"):
                        deals_db.update_deal(
                            conn, deal_id, status="rejected",
                            verification_refs=json.dumps(refs),
                            confidence=json.dumps(confidence.as_dict()))
                        deals_db.record_rejection(
                            conn, deal_id, reason="too_common",
                            note=hv.get("note", ""))
                        print(f"verify: {cand.origin}->{cand.dest} rejected — "
                              f"{hv.get('note', '')}")
                        continue
                    print(f"  histórico: {hv.get('note', '')}")

                # Legacy typical-range floor. OFF by default since it
                # proved unreachable (it demanded prices below the
                # itinerary's own 60-day minimum); kept measured so the
                # comparison with the history gate stays visible.
                floor_ok, floor_note = dealpipe.insights_floor_check(
                    cand, verify, config)
                refs["insights_floor"] = {"passed": floor_ok,
                                          "note": floor_note,
                                          "enforced": config.insights_floor}
                if config.insights_floor and floor_ok is False:
                    deals_db.update_deal(
                        conn, deal_id, status="rejected",
                        verification_refs=json.dumps(refs),
                        confidence=json.dumps(confidence.as_dict()))
                    deals_db.record_rejection(conn, deal_id,
                                              reason="thin_saving",
                                              note=floor_note)
                    print(f"verify: {cand.origin}->{cand.dest} rejected by "
                          f"the route floor ({floor_note})")
                    continue
                if cand.deal_class == "mistake" and len(
                        [f for f in confidence.families
                         if f in ("google", "ota_metasearch")]) < 2:
                    deals_db.update_deal(
                        conn, deal_id, status="verified",
                        verification_refs=json.dumps(
                            {**refs, "hold": "mistake-class needs a second "
                                             "independent live family"}),
                        confidence=json.dumps(confidence.as_dict()))
                    print("verify: mistake-class held — needs a second "
                          "independent coverage family (D2). Not published.")
                    from lib.pushes import push_mistake
                    push_mistake(cand.origin, cand.dest,
                                 verify.live_price or cand.price,
                                 cand.currency)
                    continue

                # The verified price becomes baseline history (M1): the
                # per-route percentile gate feeds on these rows.
                deals_db.insert_observations(conn, [deals_db.Observation(
                    origin=cand.origin, dest=cand.dest,
                    depart_date=cand.depart_date,
                    return_date=cand.return_date,
                    price=int(verify.live_price), currency=cand.currency,
                    source=SRC_GOOGLE if stage2 else stage1_src,
                    source_family="google",
                    found_at=None, is_verified=True)])
                baseline_median, baseline_line = dealpipe.baseline_context(
                    cand, verify.insights)
                deals_db.update_deal(
                    conn, deal_id, status="verified",
                    price=verify.live_price,
                    baseline_median=baseline_median,
                    baseline_p10=cand.baseline_p10,
                    verification_refs=json.dumps(refs),
                    confidence=json.dumps(confidence.as_dict()))
                print(f"verify: live-confirmed {verify.live_price} "
                      f"{cand.currency} ({verify.carriers}) "
                      f"[gate={cand.gate_mode}]")

                # 4. draft
                if guarded["anthropic"] is None:
                    print(f"ANTHROPIC_API_KEY missing ({drafter_err}) — deal "
                          f"#{deal_id} expired (the next run re-nominates "
                          f"the route fresh). Stop.")
                    deals_db.update_deal(conn, deal_id, status="expired")
                    status = "degraded"
                    break
                fields = dealpipe.draft_fields(cand, verify, baseline_line)
                result = guarded["anthropic"].draft(fields=fields)
                deals_db.update_deal(conn, deal_id, status="queued",
                                     draft_es=result.text,
                                     draft_version=result.template_version)

                # 5. approve: cron mode leaves the deal in the queue for
                # the /ops console; interactive mode approves right here.
                if args.queue:
                    queued_deals.append(
                        (deal_id, cand.origin, cand.dest,
                         verify.live_price, cand.currency))
                    print(f"  en cola: deal #{deal_id} espera aprobación "
                          f"en /ops")
                    continue
                _print_card(deal_id, cand, verify, result.text, confidence)
                action, reason, edited = _console_approve(args.auto_approve)
                if action == "skip":
                    print("  saltado — queda en cola ('queued')")
                    continue
                if action == "reject":
                    deals_db.update_deal(conn, deal_id, status="rejected")
                    deals_db.record_rejection(conn, deal_id, reason=reason)
                    print(f"  rechazado ({reason}) — registrado como señal "
                          f"de ajuste")
                    continue
                final_text = edited or result.text
                deals_db.update_deal(conn, deal_id, status="approved",
                                     approved_at=_now_iso(),
                                     draft_es=final_text)

                # 6. publish fan-out
                if args.skip_publish:
                    print("  --skip-publish: aprobado, sin envío")
                    continue
                targets = _publish(deal_id, cand.origin, cand.dest,
                                   verify.live_price, cand.currency,
                                   final_text)
                if not targets:
                    status = "degraded"
                summary["steps"]["publish"] = {"deal": deal_id,
                                               "targets": targets}

            if queued_deals:
                from lib.pushes import push_queued
                top = queued_deals[0]
                push_queued(len(queued_deals),
                            f"{top[1]}->{top[2]} {top[3]} {top[4]} — "
                            f"aprueba en /ops")
                summary["steps"]["queued"] = [q[0] for q in queued_deals]

            # A rail the config switches ON that could not be built ran
            # this whole cycle silently. Say so, out loud, every time.
            if dead_rails:
                status = "degraded"
                summary["dead_rails"] = dead_rails
                detail = "; ".join(dead_rails)
                LOG.error("RAILS DOWN (config says ON, key/env missing): %s",
                          detail)
                print(f"\n⚠ rails caídos: {detail}")
                if args.trigger != "local":
                    from lib.pushes import push
                    push("Vuelazo: rail caído",
                         f"Configurado pero no disponible — {detail}. "
                         f"Revisa los secrets del workflow.",
                         priority="high", tags="warning")

        except QuotaExceeded as exc:
            LOG.error("QUOTA GUARD TRIPPED: %s — planner/executor "
                      "divergence bug", exc)
            status = "degraded"
        except Exception:  # noqa: BLE001 — never leave the run dangling
            LOG.exception("deal run failed")
            status = "failed"
        finally:
            ledger.settle(run_id, SEARCH_ID)
            ledger.finalize_run(run_id, status)
            _receipt(conn, run_id)

        summary["status"] = status
        _write_summary(args, summary)
        return (EXIT_OK if status == "ok"
                else EXIT_DEGRADED if status == "degraded" else EXIT_FATAL)


def _receipt(conn, run_id: str) -> None:
    """Reserved vs RAW spend (spend_events), not the settled used_units —
    settle() clamps used to reserved, which would make the invariant
    check below unfalsifiable (it must be able to catch a guard-bypass
    bug, e.g. a metered-method-name drift after an adapter rename)."""
    rows = conn.execute(
        """
        SELECT rr.source, rr.reserved_units, rr.state,
               COALESCE((SELECT SUM(se.units) FROM spend_events se
                         WHERE se.run_id = rr.run_id
                           AND se.search_id = rr.search_id
                           AND se.source = rr.source), 0) AS spent
        FROM run_reservations rr
        WHERE rr.run_id = ? ORDER BY rr.source
        """, (run_id,)).fetchall()
    if not rows:
        return
    print("\nledger receipt (reserved vs used):")
    bad = False
    for r in rows:
        ok = r["spent"] <= r["reserved_units"]
        bad = bad or not ok
        print(f"  {r['source']:<12} reserved {r['reserved_units']:>3}  "
              f"used {r['spent']:>3}  [{r['state']}]"
              + ("" if ok else "  ← OVER!"))
    print("  invariant: used ≤ reserved on every line"
          + (" — HELD" if not bad else " — VIOLATED (bug)"))


def _write_summary(args, summary: dict) -> None:
    summary["finished_at"] = _now_iso()
    if args.json_summary:
        Path(args.json_summary).write_text(
            json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
