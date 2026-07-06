"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function LoginPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
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
      setError(body.error ?? `login failed (${r.status})`);
    }
  }

  return (
    <div className="mx-auto max-w-sm pt-16">
      <h1 className="mb-6 font-mono text-lg text-fg-bright">
        OPERATOR LOGIN
      </h1>
      <form onSubmit={submit} className="space-y-4">
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="password"
          autoFocus
          className="w-full rounded-card border border-line bg-bg-2 px-3 py-2.5 font-mono text-sm text-fg-bright outline-none focus:border-matrix-dim"
        />
        <button
          type="submit"
          disabled={busy || !password}
          className="w-full rounded-card border border-matrix-dim bg-bg-2 px-3 py-2.5 font-mono text-sm font-semibold tracking-wider text-matrix hover:shadow-glow disabled:opacity-40"
        >
          {busy ? "..." : "ENTER"}
        </button>
        {error && (
          <p className="font-mono text-[12px] text-danger">{error}</p>
        )}
      </form>
    </div>
  );
}
