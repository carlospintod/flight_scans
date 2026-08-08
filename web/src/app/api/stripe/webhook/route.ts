// Stripe webhook (M2) — the sacred path. Signature-verified against the
// RAW body, idempotent via the stripe_events PK (INSERT OR IGNORE +
// rowsAffected: only the inserting request processes), and every
// entitlement transition is logged to member_events.
//
// Failure semantics (no transactions on Turso HTTP): the idempotency
// gate is claimed FIRST (concurrency), and if the post-gate work throws
// the claim is DELETED before returning 500 — so Stripe's retry can
// reprocess instead of hitting a poisoned duplicate short-circuit.
//
// Events handled:
//   checkout.session.completed        -> grant ONLY when payment_status
//     is 'paid' (delayed methods complete unpaid first)
//   checkout.session.async_payment_succeeded -> grant (same path)
//   checkout.session.async_payment_failed    -> log, never grant
//   charge.refunded -> revoke ONLY on FULL refund (charge.refunded flag);
//     partial refunds are logged without touching the entitlement.
//     Channel removal is enforced by scripts/member_lifecycle.py.

import { NextRequest, NextResponse } from "next/server";
import Stripe from "stripe";
import { db } from "@/lib/db";
import { sendEmail } from "@/lib/email";
import {
  ensureMemberTables,
  getMemberByEmail,
  isSuppressed,
  logMemberEvent,
  mintMemberToken,
  nowIso,
  upsertMemberFromCheckout,
} from "@/lib/members";

async function grantFromSession(
  session: Stripe.Checkout.Session,
  origin: string,
): Promise<NextResponse> {
  const email = session.customer_details?.email ?? session.customer_email;
  if (!email) {
    return NextResponse.json({ ok: true, skipped: "no email" });
  }
  // Only sessions THIS app created carry the plan marker — a Payment
  // Link or any future second product on the account must not mint
  // Vuelazo passes.
  const plan = session.metadata?.plan;
  if (plan !== "founding" && plan !== "list") {
    return NextResponse.json({ ok: true, skipped: "not a vuelazo pass" });
  }
  const { member, created } = await upsertMemberFromCheckout({
    email,
    plan,
    pricePaid: session.amount_total ?? 0,
    stripeCustomerId:
      typeof session.customer === "string" ? session.customer : null,
    stripePaymentRef: session.id,
  });

  // Welcome email: magic link (fragment token, consumed on click-in)
  // + Telegram deep link that binds and admits into the channel.
  // Sent even to suppressed addresses — it carries the PAID member's
  // access links (solicited transactional mail; suppression governs
  // bulk sends, see lib/email.ts). The suppression is logged for audit.
  const loginToken = await mintMemberToken(member.id, "login", 7);
  const bindToken = await mintMemberToken(member.id, "tg_bind", 30);
  if (await isSuppressed(member.email)) {
    await logMemberEvent(member.id, "welcome_sent_despite_suppression");
  }
  const botUser = process.env.TELEGRAM_BOT_USERNAME ?? "";
  const tgLine = botUser
    ? `2) Alertas al instante en Telegram: https://t.me/${botUser}?start=${bindToken}\n`
    : "2) El enlace de Telegram llegará en un email aparte.\n";
  const mail = await sendEmail({
    to: member.email,
    subject: created
      ? "Bienvenido a Vuelazo — tu pase anual está activo"
      : "Tu pase Vuelazo se ha renovado",
    text:
      `Hola,\n\n` +
      `tu pase anual de Vuelazo está activo hasta el ${member.memberUntil.slice(0, 10)}.\n` +
      `Sin renovación automática: tú decides cada año.\n\n` +
      `1) Tu cuenta (aeropuertos, Telegram, membresía):\n` +
      `   ${origin}/cuenta#${loginToken}\n` +
      tgLine +
      `\nEl precio normal, demostrado — y el chollo, a tiempo.\nVuelazo`,
  });
  await logMemberEvent(
    member.id,
    created ? "welcome_sent" : "renewal_receipt_sent",
    mail.ok ? mail.id : `email failed: ${mail.error}`,
  );
  return NextResponse.json({ ok: true, member: member.id, created });
}

export async function POST(req: NextRequest) {
  const key = process.env.STRIPE_SECRET_KEY ?? "";
  const whSecret = process.env.STRIPE_WEBHOOK_SECRET ?? "";
  if (!key || !whSecret) {
    return NextResponse.json(
      { error: "stripe env not configured" },
      { status: 501 },
    );
  }
  const stripe = new Stripe(key);
  const sig = req.headers.get("stripe-signature") ?? "";
  const raw = await req.text();
  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(raw, sig, whSecret);
  } catch {
    return NextResponse.json({ error: "bad signature" }, { status: 400 });
  }

  await ensureMemberTables();

  // Idempotency gate: only the request that inserts the event id
  // processes it — retries and double-deliveries no-op here.
  const gate = await db().execute({
    sql: "INSERT OR IGNORE INTO stripe_events (event_id, type, processed_at) VALUES (?, ?, ?)",
    args: [event.id, event.type, nowIso()],
  });
  if (gate.rowsAffected !== 1) {
    return NextResponse.json({ ok: true, duplicate: true });
  }

  try {
    if (
      event.type === "checkout.session.completed" ||
      event.type === "checkout.session.async_payment_succeeded"
    ) {
      const session = event.data.object;
      // Delayed-notification methods complete 'unpaid' first; the grant
      // rides async_payment_succeeded in that case.
      if (
        event.type === "checkout.session.completed" &&
        session.payment_status !== "paid"
      ) {
        return NextResponse.json({ ok: true, deferred: "awaiting payment" });
      }
      return await grantFromSession(session, req.nextUrl.origin);
    }

    if (event.type === "checkout.session.async_payment_failed") {
      const session = event.data.object;
      const email = session.customer_details?.email ?? session.customer_email;
      if (email) {
        const member = await getMemberByEmail(email);
        if (member) {
          await logMemberEvent(member.id, "async_payment_failed", session.id);
        }
      }
      return NextResponse.json({ ok: true });
    }

    if (event.type === "charge.refunded") {
      const charge = event.data.object;
      const email = charge.billing_details?.email ?? charge.receipt_email;
      if (email) {
        const member = await getMemberByEmail(email);
        if (member) {
          if (charge.refunded !== true) {
            // Partial refund: a support gesture, not a cancellation.
            await logMemberEvent(
              member.id,
              "partial_refund",
              `${charge.amount_refunded}/${charge.amount} ${charge.id}`,
            );
          } else {
            await db().execute({
              sql: `UPDATE members SET status = 'refunded', member_until = ?
                    WHERE id = ? AND status != 'refunded'`,
              args: [nowIso(), member.id],
            });
            await logMemberEvent(member.id, "refunded", charge.id);
          }
        }
      }
      return NextResponse.json({ ok: true });
    }

    return NextResponse.json({ ok: true, ignored: event.type });
  } catch (err) {
    // Release the idempotency claim so Stripe's retry can reprocess —
    // otherwise a transient failure here permanently swallows a PAID
    // event (the retry would read "duplicate").
    try {
      await db().execute({
        sql: "DELETE FROM stripe_events WHERE event_id = ?",
        args: [event.id],
      });
    } catch {
      /* if even the release fails, Stripe retries against the claim;
         the event id lands in the dead-letter view of the dashboard */
    }
    const msg = err instanceof Error ? err.message : "processing failed";
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
