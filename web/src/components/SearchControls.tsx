"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/** Per-search owner controls on /searches: pause/resume + notify mode.
 *  Pausing frees the capacity slot immediately; data is kept. */
export function SearchControls({
  searchId,
  status,
  notify,
}: {
  searchId: string;
  status: string;
  notify: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function patch(body: Record<string, string>) {
    setBusy(true);
    setError(null);
    const r = await fetch(`/api/searches/${searchId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setBusy(false);
    if (r.ok) {
      router.refresh();
    } else {
      const data = await r.json().catch(() => ({}));
      setError(data.error ?? `HTTP ${r.status}`);
    }
  }

  if (status === "ended") {
    return (
      <span className="font-mono text-[11px] text-fg-dim">
        window ended — capacity freed
      </span>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        onClick={() => patch({ action: status === "active" ? "pause" : "resume" })}
        disabled={busy}
        className="rounded border border-line-bright px-2.5 py-1 font-mono text-[11px] tracking-wider text-fg hover:border-matrix-dim disabled:opacity-40"
      >
        {status === "active" ? "PAUSE" : "RESUME"}
      </button>
      <label className="flex items-center gap-1.5 font-mono text-[11px] text-fg-dim">
        push
        <select
          value={notify}
          disabled={busy}
          onChange={(e) => patch({ notify: e.target.value })}
          className="rounded border border-line bg-bg px-1.5 py-1 font-mono text-[11px] text-fg outline-none focus:border-matrix-dim"
        >
          <option value="every_run">every run</option>
          <option value="alerts_only">alerts only</option>
          <option value="off">off</option>
        </select>
      </label>
      {error && (
        <span className="font-mono text-[11px] text-danger">{error}</span>
      )}
    </div>
  );
}
