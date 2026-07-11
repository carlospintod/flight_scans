"use client";

import { useState } from "react";

export function TriggerScan() {
  const [state, setState] = useState<
    { kind: "idle" } | { kind: "busy" } | { kind: "done"; url: string } | {
      kind: "error";
      message: string;
    }
  >({ kind: "idle" });

  async function fire() {
    setState({ kind: "busy" });
    const r = await fetch("/api/trigger-scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const body = await r.json().catch(() => ({}));
    if (r.ok) {
      setState({ kind: "done", url: body.runsUrl });
    } else {
      setState({ kind: "error", message: body.error ?? `HTTP ${r.status}` });
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        onClick={fire}
        disabled={state.kind === "busy"}
        className="rounded-card border border-signature-dim bg-bg2 px-4 py-2.5 font-mono text-sm font-semibold tracking-wider text-signature hover:shadow-glow-sig disabled:opacity-40"
      >
        {state.kind === "busy" ? "DISPATCHING..." : "▶ TRIGGER SCAN NOW"}
      </button>
      {state.kind === "done" && (
        <span className="font-mono text-[12px] text-text-mid">
          dispatched —{" "}
          <a
            href={state.url}
            target="_blank"
            rel="noreferrer"
            className="text-cyan99 underline hover:text-signature"
          >
            watch the run
          </a>{" "}
          (~5–15 min; this page updates when it finishes)
        </span>
      )}
      {state.kind === "error" && (
        <span className="font-mono text-[12px] text-red">
          {state.message}
        </span>
      )}
    </div>
  );
}
