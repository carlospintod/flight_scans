// Resend plain-email sends from the web tier (M2): welcome + magic
// links. Bulk sends (alerts, digest, reminders) live on the Python side
// where the quota ledger meters them.
//
// Suppression contract (non-negotiable #7, interpreted): the
// suppression list gates every BULK send (alerts, digest, reminders —
// all Python-side, all checked). Web-tier mail is SOLICITED
// transactional access mail (a purchase or an explicit login request
// seconds earlier) and sends regardless — withholding a paid member's
// access links over an old newsletter unsubscribe would break the
// product. Audit trail: the webhook logs welcome_sent_despite_suppression.
//
// RESEND_API_KEY is a Vercel env secret (never source_credentials).

const RESEND_URL = "https://api.resend.com/emails";

export const EMAIL_FROM =
  process.env.EMAIL_FROM ?? "Vuelazo <onboarding@resend.dev>";

export async function sendEmail(opts: {
  to: string;
  subject: string;
  text: string;
  headers?: Record<string, string>;
}): Promise<{ ok: boolean; id?: string; error?: string }> {
  const key = process.env.RESEND_API_KEY ?? "";
  if (!key) return { ok: false, error: "RESEND_API_KEY not configured" };
  try {
    const r = await fetch(RESEND_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: EMAIL_FROM,
        to: [opts.to],
        subject: opts.subject,
        text: opts.text,
        ...(opts.headers ? { headers: opts.headers } : {}),
      }),
      cache: "no-store",
    });
    const data = (await r.json().catch(() => ({}))) as {
      id?: string;
      message?: string;
    };
    if (!r.ok) return { ok: false, error: data.message ?? `HTTP ${r.status}` };
    return { ok: true, id: data.id };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "network" };
  }
}
