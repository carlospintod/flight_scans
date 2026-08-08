"use client";

// Free-list signup (M4a) — the trust engine's front door.

import { useState } from "react";

export default function SubscribeForm({ compact = false }: { compact?: boolean }) {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "busy" | "done" | { error: string }>(
    "idle",
  );

  async function subscribe() {
    setState("busy");
    try {
      const r = await fetch("/api/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const data = (await r.json().catch(() => ({}))) as { error?: string };
      if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`);
      setState("done");
    } catch (e) {
      setState({ error: e instanceof Error ? e.message : "error" });
    }
  }

  if (state === "done") {
    return (
      <p className="text-[15px] font-semibold text-vz-ink">
        ✓ Dentro. Los mejores chollos de la semana, cada domingo.
      </p>
    );
  }
  return (
    <div>
      <div className="flex max-w-md gap-2">
        <input
          type="email"
          placeholder="tu@email.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-md border border-vz-line bg-white px-3 py-2.5 text-[15px] text-vz-ink outline-none placeholder:text-vz-ink-soft focus:border-vz-amber"
        />
        <button
          onClick={() => void subscribe()}
          disabled={state === "busy" || !email.includes("@")}
          className="whitespace-nowrap rounded-md bg-vz-amber px-4 py-2.5 text-[15px] font-semibold text-vz-paper hover:bg-vz-amber-deep disabled:opacity-40"
        >
          {state === "busy" ? "…" : "Apúntame gratis"}
        </button>
      </div>
      {!compact && (
        <p className="mt-2 text-[12px] text-vz-ink-soft">
          1–3 chollos excelentes por semana, 24 h después que los miembros.
          Sin spam. Baja en un clic.
        </p>
      )}
      {typeof state === "object" && (
        <p className="mt-2 text-[13px] text-vz-amber-deep">{state.error}</p>
      )}
    </div>
  );
}
