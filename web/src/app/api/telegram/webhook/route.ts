// Telegram bot webhook (M2, D4): the deep-link bind flow.
//
//   welcome email -> t.me/<bot>?start=<tg_bind token> -> Telegram sends
//   "/start <token>" here -> CAS-consume the token -> bind telegram_user_id
//   -> mint a SINGLE-USE invite link into the private channel -> reply.
//
// Native Bot API, no third-party gatekeepers. The webhook is registered
// once with a secret_token (scripts/setup_telegram_webhook.py); every
// request must echo it in x-telegram-bot-api-secret-token. Always
// answer 200 (Telegram retries non-200s forever).

import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import {
  consumeMemberToken,
  ensureMemberTables,
  getMember,
  logMemberEvent,
  peekMemberToken,
} from "@/lib/members";

const API = "https://api.telegram.org";

async function tg(method: string, body: Record<string, unknown>) {
  const token = process.env.TELEGRAM_BOT_TOKEN ?? "";
  if (!token) return null;
  try {
    const r = await fetch(`${API}/bot${token}/${method}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const data = (await r.json().catch(() => null)) as {
      ok?: boolean;
      result?: unknown;
    } | null;
    return data?.ok ? data.result : null;
  } catch {
    return null;
  }
}

export async function POST(req: NextRequest) {
  const expected = process.env.TELEGRAM_WEBHOOK_SECRET ?? "";
  if (!expected) {
    return NextResponse.json({ error: "not configured" }, { status: 501 });
  }
  if (req.headers.get("x-telegram-bot-api-secret-token") !== expected) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }

  let update: {
    message?: {
      text?: string;
      chat?: { id: number; type: string };
      from?: { id: number };
    };
  };
  try {
    update = await req.json();
  } catch {
    return NextResponse.json({ ok: true });
  }
  const msg = update.message;
  if (!msg?.chat || msg.chat.type !== "private" || !msg.from) {
    return NextResponse.json({ ok: true });
  }
  const chatId = msg.chat.id;
  const text = msg.text ?? "";
  const m = /^\/start\s+([0-9a-f]{48})$/.exec(text.trim());
  if (!m) {
    await tg("sendMessage", {
      chat_id: chatId,
      text:
        "Hola 👋 Soy el bot de Vuelazo. Para entrar en el canal de " +
        "miembros usa el enlace de tu email de bienvenida " +
        "(vuelazo.es/cuenta si lo has perdido).",
    });
    return NextResponse.json({ ok: true });
  }

  await ensureMemberTables();
  // Peek BEFORE consuming: a lapsed member's tap must not burn their
  // single-use deep link — after renewing, the same link must work.
  const peekedId = await peekMemberToken(m[1], "tg_bind");
  if (peekedId == null) {
    await tg("sendMessage", {
      chat_id: chatId,
      text:
        "Ese enlace ya se usó o ha caducado. Entra en tu cuenta " +
        "(vuelazo.es/cuenta) y pide uno nuevo.",
    });
    return NextResponse.json({ ok: true });
  }
  const member = await getMember(peekedId);
  if (!member || member.status !== "active") {
    await tg("sendMessage", {
      chat_id: chatId,
      text:
        "Tu membresía no está activa ahora mismo. Renueva en " +
        "vuelazo.es/unete y vuelve a tocar este mismo enlace: seguirá " +
        "funcionando.",
    });
    return NextResponse.json({ ok: true });
  }
  const memberId = await consumeMemberToken(m[1], "tg_bind");
  if (memberId == null) {
    // Raced with a concurrent consume — treat as already-used.
    await tg("sendMessage", {
      chat_id: chatId,
      text: "Ese enlace ya se usó. Si no fuiste tú, escríbenos: hola@vuelazo.es.",
    });
    return NextResponse.json({ ok: true });
  }

  await db().execute({
    sql: "UPDATE members SET telegram_user_id = ? WHERE id = ?",
    args: [msg.from.id, memberId],
  });
  await logMemberEvent(memberId, "tg_bound", String(msg.from.id));

  const channel =
    process.env.TELEGRAM_PRIVATE_CHANNEL_ID ??
    process.env.TELEGRAM_TEST_CHAT_ID ??
    "";
  let invite: { invite_link?: string } | null = null;
  if (channel) {
    // Defensive unban first: a crashed ban→unban pair from a past lapse
    // would otherwise make the invite link unusable for a renewed
    // member (no-op for non-banned users).
    await tg("unbanChatMember", {
      chat_id: channel,
      user_id: msg.from.id,
      only_if_banned: true,
    });
    invite = (await tg("createChatInviteLink", {
      chat_id: channel,
      member_limit: 1,
      expire_date: Math.floor(Date.now() / 1000) + 86400,
    })) as { invite_link?: string } | null;
  }
  if (invite?.invite_link) {
    await tg("sendMessage", {
      chat_id: chatId,
      text: `✅ Cuenta vinculada. Tu invitación al canal de miembros (un solo uso, 24h):\n${invite.invite_link}`,
    });
  } else {
    // Honest failure: no phantom "llegará en breve". The event gives
    // ops a queryable marker; the member gets a real next step.
    await logMemberEvent(memberId, "tg_invite_failed", channel || "no channel");
    await tg("sendMessage", {
      chat_id: chatId,
      text:
        "✅ Cuenta vinculada, pero no he podido generar tu invitación al " +
        "canal ahora mismo. Escríbenos a hola@vuelazo.es y te la " +
        "mandamos enseguida.",
    });
  }
  return NextResponse.json({ ok: true });
}
