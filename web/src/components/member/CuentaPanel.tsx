"use client";

// /cuenta client side (M2/M4a): fragment-token consume, magic-link
// request, airport preferences, logout — in the Vuelazo paper theme.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

type MemberView = {
  id: number;
  email: string;
  status: string;
  memberUntil: string;
  plan: string;
  telegramBound: boolean;
  airports: string[];
  suppressed: boolean;
};

const ALL = ["MAD", "BCN", "VLC", "ALC"] as const;

const btnPrimary =
  "rounded-md bg-vz-amber px-4 py-2.5 text-[15px] font-semibold text-vz-paper hover:bg-vz-amber-deep disabled:opacity-40";
const input =
  "w-full rounded-md border border-vz-line bg-white px-3 py-2.5 text-[15px] text-vz-ink outline-none placeholder:text-vz-ink-soft focus:border-vz-amber";
const panel = "rounded-lg border border-vz-line bg-white/60 p-5";
const eyebrow =
  "text-[11px] font-semibold uppercase tracking-[2px] text-vz-ink-soft";

export default function CuentaPanel({ member }: { member: MemberView | null }) {
  const router = useRouter();

  const [token, setToken] = useState<string | null>(null);
  const [state, setState] = useState<
    "idle" | "busy" | "sent" | { error: string }
  >("idle");
  const [email, setEmail] = useState("");

  useEffect(() => {
    const hash = window.location.hash.replace(/^#/, "");
    setToken(/^[0-9a-f]{48}$/.test(hash) ? hash : null);
  }, []);

  async function consume() {
    if (!token) return;
    setState("busy");
    const r = await fetch("/api/member/consume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (r.ok) {
      window.location.hash = "";
      router.refresh();
      setState("idle");
    } else {
      const body = await r.json().catch(() => ({}));
      setState({ error: body.error ?? `HTTP ${r.status}` });
    }
  }

  async function requestLink() {
    setState("busy");
    const r = await fetch("/api/member/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    if (r.ok) setState("sent");
    else {
      const body = await r.json().catch(() => ({}));
      setState({ error: body.error ?? `HTTP ${r.status}` });
    }
  }

  const [airports, setAirports] = useState<string[]>(member?.airports ?? []);
  const [prefState, setPrefState] = useState<
    "idle" | "busy" | "saved" | { error: string }
  >("idle");

  async function saveAirports(next: string[]) {
    setAirports(next);
    setPrefState("busy");
    const r = await fetch("/api/member/airports", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ airports: next }),
    });
    if (r.ok) setPrefState("saved");
    else {
      const body = await r.json().catch(() => ({}));
      setPrefState({ error: body.error ?? `HTTP ${r.status}` });
    }
  }

  if (!member) {
    return (
      <div className="mx-auto max-w-md pt-8 text-center">
        <h1 className="font-vz-display text-3xl font-semibold">Tu cuenta</h1>
        {token ? (
          <>
            <p className="mt-5 text-[15px] text-vz-ink-soft">
              Este enlace te conecta con tu cuenta de Vuelazo. Funciona
              una vez.
            </p>
            <button
              onClick={() => void consume()}
              disabled={state === "busy"}
              className={`mt-6 ${btnPrimary}`}
            >
              {state === "busy" ? "…" : "Entrar →"}
            </button>
          </>
        ) : (
          <>
            <p className="mt-5 text-[15px] text-vz-ink-soft">
              Te enviamos un enlace de acceso al email de tu membresía.
            </p>
            <div className="mt-4 flex gap-2">
              <input
                className={input}
                type="email"
                placeholder="tu@email.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <button
                onClick={() => void requestLink()}
                disabled={state === "busy" || !email.includes("@")}
                className={btnPrimary}
              >
                Enviar
              </button>
            </div>
            {state === "sent" && (
              <p className="mt-4 text-[13px] font-semibold text-vz-ink">
                ✓ Si esa dirección tiene membresía, el enlace va de camino.
              </p>
            )}
          </>
        )}
        {typeof state === "object" && (
          <p className="mt-4 text-[13px] text-vz-amber-deep">{state.error}</p>
        )}
      </div>
    );
  }

  const active = member.status === "active";
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="font-vz-display text-3xl font-semibold">Tu cuenta</h1>
        <p className="mt-1 text-[13px] text-vz-ink-soft">{member.email}</p>
      </div>

      <section className={panel}>
        <p className={eyebrow}>Membresía</p>
        <p className="mt-2 text-[15px] font-semibold text-vz-ink">
          {active ? "activa" : member.status} ·{" "}
          {member.plan === "founding"
            ? "precio fundador (29 €)"
            : "pase anual (39 €)"}
        </p>
        <p className="mt-1 text-[13px] text-vz-ink-soft">
          válida hasta {member.memberUntil.slice(0, 10)} — sin renovación
          automática
        </p>
        <a
          href="/unete"
          className="mt-3 inline-block rounded-md border border-vz-line px-3 py-1.5 text-[13px] font-semibold text-vz-ink hover:border-vz-amber"
        >
          Renovar / reactivar →
        </a>
      </section>

      <section className={panel}>
        <p className={eyebrow}>Tus aeropuertos</p>
        <p className="mt-1 text-[13px] text-vz-ink-soft">
          Solo recibirás alertas de salidas desde los que marques.
        </p>
        <div className="mt-3 flex flex-wrap gap-4">
          {ALL.map((a) => (
            <label
              key={a}
              className="flex items-center gap-1.5 text-[14px] font-semibold text-vz-ink"
            >
              <input
                type="checkbox"
                className="accent-vz-amber"
                checked={airports.includes(a)}
                onChange={(e) => {
                  const next = e.target.checked
                    ? [...airports, a]
                    : airports.filter((x) => x !== a);
                  if (next.length > 0) void saveAirports(next);
                }}
              />
              {a}
            </label>
          ))}
        </div>
        {prefState === "saved" && (
          <p className="mt-2 text-[12px] font-semibold text-vz-ink">
            guardado ✓
          </p>
        )}
        {typeof prefState === "object" && (
          <p className="mt-2 text-[12px] text-vz-amber-deep">
            {prefState.error}
          </p>
        )}
      </section>

      <section className={panel}>
        <p className={eyebrow}>Telegram</p>
        <p className="mt-2 text-[14px] text-vz-ink">
          {member.telegramBound
            ? "✓ vinculado — las alertas llegan al canal privado"
            : "sin vincular — usa el enlace de tu email de bienvenida"}
        </p>
      </section>

      <section className={panel}>
        <p className={eyebrow}>Emails</p>
        <p className="mt-2 text-[14px] text-vz-ink">
          {member.suppressed
            ? "⏸ pausados (lista de supresión) — escríbenos para reactivarlos"
            : "activos: alertas de tus aeropuertos + avisos de renovación"}
        </p>
      </section>

      <button
        onClick={async () => {
          await fetch("/api/member/logout", { method: "POST" });
          router.refresh();
        }}
        className="rounded-md border border-vz-line px-4 py-2 text-[13px] text-vz-ink-soft hover:border-vz-amber hover:text-vz-ink"
      >
        Cerrar sesión
      </button>
    </div>
  );
}
