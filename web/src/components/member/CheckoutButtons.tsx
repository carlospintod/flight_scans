"use client";

// Checkout entry (M2/M4a): plan pick -> Stripe Checkout redirect.

import { useState } from "react";

type PayState = "idle" | "founding" | "list" | { error: string };

export default function CheckoutButtons() {
  const [state, setState] = useState<PayState>("idle");

  async function pay(plan: "founding" | "list") {
    setState(plan);
    try {
      const r = await fetch("/api/stripe/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan }),
      });
      const data = (await r.json()) as { url?: string; error?: string };
      if (!r.ok || !data.url) throw new Error(data.error ?? `HTTP ${r.status}`);
      window.location.href = data.url;
    } catch (e) {
      setState({ error: e instanceof Error ? e.message : "error" });
    }
  }

  const busy = state === "founding" || state === "list";
  return (
    <div>
      <div className="grid gap-4 sm:grid-cols-2">
        <button
          onClick={() => void pay("founding")}
          disabled={busy}
          className="rounded-lg border-2 border-vz-amber bg-white/70 p-5 text-left hover:bg-white disabled:opacity-40"
        >
          <p className="text-[11px] font-semibold uppercase tracking-[2px] text-vz-amber-deep">
            Precio fundador
          </p>
          <p className="mt-2 font-vz-display text-4xl font-semibold text-vz-ink">
            29€
            <span className="text-base font-normal text-vz-ink-soft">
              /año · IVA incl.
            </span>
          </p>
          <p className="mt-2 text-[13px] leading-relaxed text-vz-ink-soft">
            Para los primeros. Lo mantienes cada año si renuevas a tiempo.
          </p>
        </button>
        <button
          onClick={() => void pay("list")}
          disabled={busy}
          className="rounded-lg border border-vz-line bg-white/70 p-5 text-left hover:bg-white disabled:opacity-40"
        >
          <p className="text-[11px] font-semibold uppercase tracking-[2px] text-vz-ink-soft">
            Precio normal
          </p>
          <p className="mt-2 font-vz-display text-4xl font-semibold text-vz-ink">
            39€
            <span className="text-base font-normal text-vz-ink-soft">
              /año · IVA incl.
            </span>
          </p>
          <p className="mt-2 text-[13px] leading-relaxed text-vz-ink-soft">
            Pase anual completo, sin ataduras.
          </p>
        </button>
      </div>
      <p className="mt-4 text-[12px] leading-relaxed text-vz-ink-soft">
        Pago único con tarjeta o Bizum · sin renovación automática — tú
        decides cada año · garantía de devolución de 14 días.
      </p>
      {typeof state === "object" && (
        <p className="mt-3 text-[13px] text-vz-amber-deep">{state.error}</p>
      )}
      {busy && (
        <p className="mt-3 text-[13px] text-vz-ink-soft">
          abriendo el pago seguro de Stripe…
        </p>
      )}
    </div>
  );
}
