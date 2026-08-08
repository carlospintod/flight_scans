// Stripe Checkout session (M2, D5): one-time 12-month pass, no
// auto-renewal anywhere. Prices are IVA-included (Stripe Tax handles
// the breakdown); Bizum availability is controlled in the Stripe
// dashboard (payment methods are left automatic so enabling it there
// needs no deploy).
//
// STRIPE_SECRET_KEY lives in Vercel env (test key until Phase-3 W13).

import { NextRequest, NextResponse } from "next/server";
import Stripe from "stripe";
import { z } from "zod";

// cents, IVA incluido (D5 [R]): list 39, founding 29 — annual only.
const PRICES: Record<string, { amount: number; label: string }> = {
  founding: { amount: 2900, label: "Vuelazo — pase anual (precio fundador)" },
  list: { amount: 3900, label: "Vuelazo — pase anual" },
};

const bodySchema = z.object({
  plan: z.enum(["founding", "list"]),
  email: z.string().email().optional(),
});

export async function POST(req: NextRequest) {
  const key = process.env.STRIPE_SECRET_KEY ?? "";
  if (!key) {
    return NextResponse.json(
      { error: "STRIPE_SECRET_KEY not configured on the server" },
      { status: 501 },
    );
  }
  let body: z.infer<typeof bodySchema>;
  try {
    body = bodySchema.parse(await req.json());
  } catch {
    return NextResponse.json({ error: "bad body" }, { status: 400 });
  }
  // The founding price is a cohort price (D5). It launches open; when
  // Carlos closes the window it's an env flip (FOUNDING_OPEN=false on
  // Vercel), not a deploy — after that, renewals keep it via the
  // renewal email's founding link policy, handled at support level.
  if (body.plan === "founding" && process.env.FOUNDING_OPEN === "false") {
    return NextResponse.json(
      { error: "el precio fundador ya no está disponible" },
      { status: 403 },
    );
  }
  const price = PRICES[body.plan];
  const origin = req.nextUrl.origin;
  const stripe = new Stripe(key);
  try {
    const session = await stripe.checkout.sessions.create({
      mode: "payment", // one-time — "sin renovación automática" (D5)
      line_items: [
        {
          quantity: 1,
          price_data: {
            currency: "eur",
            unit_amount: price.amount,
            tax_behavior: "inclusive",
            product_data: { name: price.label },
          },
        },
      ],
      automatic_tax: { enabled: true }, // Stripe Tax from the first sale (D5)
      customer_creation: "always",
      ...(body.email ? { customer_email: body.email } : {}),
      metadata: { plan: body.plan },
      success_url: `${origin}/gracias?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${origin}/unete`,
      locale: "es",
    });
    return NextResponse.json({ ok: true, url: session.url });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "stripe error";
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}
