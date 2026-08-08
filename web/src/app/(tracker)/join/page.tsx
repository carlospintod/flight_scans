"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

/** Landing page for invite/login links. The one-time token travels in
 *  the URL FRAGMENT (#...) so it never reaches server logs, and it is
 *  consumed only on an explicit button POST — a link scanner's GET
 *  can't burn it. */
export default function JoinPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [state, setState] = useState<
    "idle" | "busy" | { error: string }
  >("idle");

  useEffect(() => {
    const hash = window.location.hash.replace(/^#/, "");
    setToken(/^[0-9a-f]{48}$/.test(hash) ? hash : null);
  }, []);

  async function enter() {
    if (!token) return;
    setState("busy");
    const r = await fetch("/api/auth/consume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (r.ok) {
      window.location.hash = "";
      router.push("/searches");
      router.refresh();
    } else {
      const body = await r.json().catch(() => ({}));
      setState({ error: body.error ?? `HTTP ${r.status}` });
    }
  }

  return (
    <div className="mx-auto max-w-md pt-16 text-center">
      <h1 className="cursor-blink font-mono text-xl font-semibold tracking-[2px] text-text-bright">
        FLIGHT_SCANS
      </h1>
      {token === null ? (
        <p className="mt-6 font-mono text-sm text-text-mid">
          FlightScans is invite-only. If you were sent an invite link,
          make sure you opened it in full — and if your session expired,
          ask the owner for a fresh link.
        </p>
      ) : (
        <>
          <p className="mt-6 text-sm text-text">
            You&apos;ve been invited to track flexible-date flight prices.
            This link signs you in — it works once.
          </p>
          <button
            onClick={enter}
            disabled={state === "busy"}
            className="mt-6 rounded-card border border-signature-dim bg-bg2 px-4 py-2.5 font-mono text-sm font-semibold tracking-wider text-signature hover:shadow-glow-sig disabled:opacity-40"
          >
            {state === "busy" ? "..." : "ENTER →"}
          </button>
          {typeof state === "object" && (
            <p className="mt-4 font-mono text-[12px] text-red">
              {state.error}
            </p>
          )}
        </>
      )}
    </div>
  );
}
