import { ageDays, freshness } from "@/lib/format";
import type { ScanRun } from "@/lib/types";

const STYLES = {
  fresh: { dot: "bg-matrix shadow-glow", text: "text-fg-mid" },
  aging: { dot: "bg-amber", text: "text-amber" },
  stale: { dot: "bg-danger", text: "text-danger" },
} as const;

export function StaleBadge({ run }: { run: ScanRun | null }) {
  const at = run?.finishedAt ?? run?.startedAt ?? null;
  const f = freshness(at);
  const s = STYLES[f];
  const label = at
    ? (() => {
        const d = ageDays(at);
        return d === 0 ? "last scan today" : `last scan ${d}d ago`;
      })()
    : "no scans recorded";
  return (
    <span
      className={`inline-flex items-center gap-2 font-mono text-[11px] tracking-wider ${s.text}`}
      title={
        run
          ? `${run.trigger} scan, ${run.rowsStored} prices stored, status ${run.status}`
          : undefined
      }
    >
      <span className={`h-2 w-2 rounded-full ${s.dot}`} />
      {label.toUpperCase()}
      {run && run.status !== "ok" ? ` (${run.status.toUpperCase()})` : ""}
    </span>
  );
}
