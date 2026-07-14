"use client";

import { useEffect, useState } from "react";

interface CredentialRow {
  envVar: string;
  label: string;
  sources: string;
  isSet: boolean;
  masked: string;
  updatedAt: string | null;
}

/** Owner console: manage the free-source API keys. Keys are stored in
 *  the DB (the scanner loads them into env at startup, DB over env) and
 *  shown MASKED — the full value is never returned once saved. Set a
 *  blank value to clear a key. */
export function CredentialsAdmin() {
  const [rows, setRows] = useState<CredentialRow[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const r = await fetch("/api/admin/credentials");
    if (r.ok) setRows((await r.json()).credentials);
  }
  useEffect(() => {
    void load();
  }, []);

  async function save(envVar: string) {
    setBusy(envVar);
    setError(null);
    setSaved(null);
    const r = await fetch("/api/admin/credentials", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ envVar, value: drafts[envVar] ?? "" }),
    });
    const data = await r.json().catch(() => ({}));
    setBusy(null);
    if (!r.ok) {
      setError(data.error ?? `HTTP ${r.status}`);
      return;
    }
    setSaved(envVar);
    setDrafts((d) => ({ ...d, [envVar]: "" }));
    void load();
  }

  return (
    <div className="space-y-3">
      {rows.map((c) => (
        <div
          key={c.envVar}
          className="rounded-card border border-line bg-bg-2 p-3"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="font-mono text-[13px] text-fg-bright">
              {c.label}
              <span className="ml-2 text-[10px] text-fg-dim">{c.envVar}</span>
            </span>
            <span className="font-mono text-[11px] tracking-wider">
              {c.isSet ? (
                <span className="text-good">set · {c.masked}</span>
              ) : (
                <span className="text-amber">not set · using env</span>
              )}
            </span>
          </div>
          <div className="mt-1 font-mono text-[10px] text-fg-dim">
            {c.sources}
            {c.updatedAt && ` · updated ${c.updatedAt.slice(0, 10)}`}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              type="password"
              value={drafts[c.envVar] ?? ""}
              onChange={(e) =>
                setDrafts((d) => ({ ...d, [c.envVar]: e.target.value }))
              }
              placeholder={c.isSet ? "paste a new key to rotate" : "paste key"}
              className="min-w-0 flex-1 rounded-card border border-line bg-bg px-2.5 py-2 font-mono text-[13px] text-fg-bright outline-none focus:border-matrix-dim"
            />
            <button
              onClick={() => void save(c.envVar)}
              disabled={busy === c.envVar || (drafts[c.envVar] ?? "") === ""}
              className="rounded border border-line-bright px-2.5 py-1 font-mono text-[11px] tracking-wider text-fg hover:border-matrix-dim disabled:opacity-40"
            >
              {busy === c.envVar ? "SAVING…" : c.isSet ? "ROTATE" : "SAVE"}
            </button>
            {c.isSet && (
              <button
                onClick={() => {
                  setDrafts((d) => ({ ...d, [c.envVar]: "" }));
                  void fetch("/api/admin/credentials", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ envVar: c.envVar, value: "" }),
                  }).then(() => load());
                }}
                className="rounded border border-line px-2.5 py-1 font-mono text-[11px] tracking-wider text-danger/80 hover:border-danger"
              >
                CLEAR
              </button>
            )}
          </div>
          {saved === c.envVar && (
            <p className="mt-1 font-mono text-[11px] text-matrix">
              saved — the next scan uses this key
            </p>
          )}
        </div>
      ))}
      {error && <p className="font-mono text-[12px] text-danger">{error}</p>}
      <p className="font-mono text-[10px] text-fg-dim">
        Keys are stored in the private DB and shown masked (last 4 only).
        A blank save clears a key (the scan falls back to the platform
        env). Infra secrets are managed in GitHub/Vercel, not here.
      </p>
    </div>
  );
}
