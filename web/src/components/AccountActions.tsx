"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function AccountActions({ isOwner }: { isOwner: boolean }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function logout() {
    await fetch("/api/logout", { method: "POST" });
    router.push("/");
    router.refresh();
  }

  async function deleteAccount() {
    const r = await fetch("/api/account", { method: "DELETE" });
    if (r.ok) {
      router.push("/");
      router.refresh();
    } else {
      const body = await r.json().catch(() => ({}));
      setError(body.error ?? `HTTP ${r.status}`);
    }
  }

  return (
    <div className="space-y-3">
      <button
        onClick={logout}
        className="rounded-card border border-line-bright bg-bg-2 px-4 py-2 font-mono text-[13px] tracking-wider text-fg-bright hover:border-matrix-dim"
      >
        SIGN OUT
      </button>
      {!isOwner && (
        <div>
          {!confirming ? (
            <button
              onClick={() => setConfirming(true)}
              className="rounded-card border border-line bg-bg-2 px-4 py-2 font-mono text-[13px] tracking-wider text-danger/80 hover:border-danger"
            >
              DELETE ACCOUNT…
            </button>
          ) : (
            <div className="rounded-card border border-danger/50 bg-bg-2 p-3">
              <p className="font-mono text-[12px] text-fg">
                This permanently deletes your account, searches, and all
                collected prices. No undo.
              </p>
              <div className="mt-2 flex gap-2">
                <button
                  onClick={deleteAccount}
                  className="rounded-card border border-danger px-3 py-1.5 font-mono text-[12px] tracking-wider text-danger"
                >
                  DELETE EVERYTHING
                </button>
                <button
                  onClick={() => setConfirming(false)}
                  className="rounded-card border border-line px-3 py-1.5 font-mono text-[12px] tracking-wider text-fg-mid"
                >
                  CANCEL
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {error && (
        <p className="font-mono text-[12px] text-danger">{error}</p>
      )}
    </div>
  );
}
