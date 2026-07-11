"use client";

import { useEffect, useState } from "react";

interface AdminUser {
  userId: number;
  email: string | null;
  displayName: string | null;
  role: string;
  activeSearches: number;
}

/** Owner console: list users, invite new ones, mint re-login links.
 *  Links are shown ONCE and copied manually (WhatsApp) — link-based
 *  auth, no email infrastructure. */
export function UserAdmin() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [minted, setMinted] = useState<{ forId: number; url: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const r = await fetch("/api/admin/users");
    if (r.ok) setUsers((await r.json()).users);
  }
  useEffect(() => { void load(); }, []);

  async function mint(body: Record<string, unknown>, forId: number) {
    setError(null);
    setMinted(null);
    const r = await fetch("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      setError(data.error ?? `HTTP ${r.status}`);
      return;
    }
    setMinted({ forId: data.userId,
                url: `${window.location.origin}${data.joinPath}` });
    setEmail("");
    setName("");
    void load();
  }

  return (
    <div className="space-y-4">
      <table className="w-full border-collapse font-mono text-[12px]">
        <thead>
          <tr className="border-b border-border-bright text-left text-[10px] uppercase tracking-wider text-hint">
            <th className="py-2 pr-3">#</th>
            <th className="py-2 pr-3">Name</th>
            <th className="py-2 pr-3">Email</th>
            <th className="py-2 pr-3">Role</th>
            <th className="py-2 pr-3">Searches</th>
            <th className="py-2">Login link</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.userId} className="border-b border-border">
              <td className="py-2 pr-3 text-hint">{u.userId}</td>
              <td className="py-2 pr-3">{u.displayName ?? "—"}</td>
              <td className="py-2 pr-3 text-text-mid">{u.email ?? "—"}</td>
              <td className="py-2 pr-3">{u.role}</td>
              <td className="py-2 pr-3">{u.activeSearches}</td>
              <td className="py-2">
                <button
                  onClick={() => mint({ userId: u.userId }, u.userId)}
                  className="rounded-card border border-border-bright px-2 py-1 text-[11px] tracking-wider text-text hover:border-signature-dim"
                >
                  MINT LINK
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {minted && (
        <div className="rounded-card border border-signature-dim bg-bg2 p-3 font-mono text-[12px]">
          <div className="text-hint">
            one-time link for user #{minted.forId} — copy it now, it is not
            shown again:
          </div>
          <div className="mt-1 flex items-center gap-2">
            <code className="break-all text-signature">{minted.url}</code>
            <button
              onClick={() => navigator.clipboard.writeText(minted.url)}
              className="shrink-0 rounded-card border border-border-bright px-2 py-1 text-[11px] text-text hover:border-signature-dim"
            >
              COPY
            </button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-end gap-2">
        <div>
          <label className="block font-mono text-[10px] uppercase tracking-wider text-hint">
            invite email
          </label>
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="friend@example.com"
            className="rounded-card border border-border bg-bg px-2.5 py-2 font-mono text-[13px] text-text-bright outline-none focus:border-signature-dim"
          />
        </div>
        <div>
          <label className="block font-mono text-[10px] uppercase tracking-wider text-hint">
            name
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Friend"
            className="rounded-card border border-border bg-bg px-2.5 py-2 font-mono text-[13px] text-text-bright outline-none focus:border-signature-dim"
          />
        </div>
        <button
          onClick={() => mint({ email, displayName: name }, 0)}
          disabled={!email.includes("@")}
          className="rounded-card border border-signature-dim bg-bg2 px-4 py-2.5 font-mono text-[13px] font-semibold tracking-wider text-signature hover:shadow-glow-sig disabled:opacity-40"
        >
          + INVITE
        </button>
      </div>
      {error && <p className="font-mono text-[12px] text-red">{error}</p>}
    </div>
  );
}
