"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function LoginPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<{ text: string; lockedOut: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    setBusy(false);
    if (r.ok) {
      router.push("/ops");
      router.refresh();
    } else {
      const body = await r.json().catch(() => ({}));
      // 429 = the per-IP limit (10 tries / 15 min, /api/login): retrying
      // now cannot work, so say so instead of "wrong password" again.
      setError({
        text: body.error ?? `login failed (${r.status})`,
        lockedOut: r.status === 429,
      });
    }
  }

  return (
    <div className="mx-auto max-w-sm pt-16">
      <h1 className="font-mono text-lg text-text-bright">OPERATOR LOGIN</h1>
      <p className="mb-6 mt-2 font-mono text-[11px] leading-4 text-hint">
        The password is APP_PASSWORD in the Vercel project&apos;s
        environment variables.
      </p>
      <form onSubmit={submit} className="space-y-4">
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="password"
          autoFocus
          className="w-full rounded-card border border-border bg-bg2 px-3 py-2.5 font-mono text-sm text-text-bright outline-none focus:border-signature-dim"
        />
        <button
          type="submit"
          disabled={busy || !password}
          className="w-full rounded-card border border-signature-dim bg-bg2 px-4 py-2.5 font-mono text-sm font-semibold tracking-wider text-signature hover:shadow-glow-sig disabled:opacity-40"
        >
          {busy ? "..." : "ENTER"}
        </button>
        {error && (
          <p
            className={`font-mono text-[12px] ${
              error.lockedOut ? "text-amber" : "text-red"
            }`}
          >
            {error.text}
          </p>
        )}
      </form>
    </div>
  );
}
