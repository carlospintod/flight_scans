import { ageDays, freshness } from "@/lib/format";
import type { DealCounts } from "@/lib/deals";
import type { QuotaCard, SpendRow } from "@/lib/queries";
import type { ScanRun, SourceHealthCard } from "@/lib/types";

/** /ops overview pieces: the "do I need to act?" tiles and the merged
 *  per-source table. Pure presentation over data the page already loads
 *  — no queries here. */

// ---------------------------------------------------------------------------
// Sources: which project pays for each, and plain labels.
// ---------------------------------------------------------------------------

// Two projects share one ledger. `_vz` pools (and the services only
// Vuelazo uses) are Vuelazo's budget; unsuffixed pools are the Nairobi
// tracker's free tier. /ops shows them in separate tables so "SerpAPI:
// 40 left" always says WHOSE 40 — a shared key drained by surprise is the
// failure this guards against.
const VUELAZO_ONLY = new Set(["anthropic", "telegram", "resend"]);

export function isVuelazoSource(source: string): boolean {
  return source.endsWith("_vz") || VUELAZO_ONLY.has(source);
}

const SOURCE_LABELS: Record<string, string> = {
  // Tracker pool: calendar sweep (every date pair, ~2-weekly) + the
  // per-scan date grid + OTA seller check.
  serpapi: "SerpAPI · calendar sweep + grid",
  serpapi_ota: "SerpAPI · OTA sellers",
  kiwi: "Kiwi · discovery",
  aviasales: "Aviasales · cached scout",
  skyscanner: "Sky Scrapper · curve",
  searchapi: "SearchAPI · break-glass",
  googleflights: "Google Flights · free scraper",
  serpapi_vz: "SerpAPI · verify",
  googleflights_vz: "Google Flights · free scraper",
  searchapi_vz: "SearchAPI · sweep",
  aviasales_vz: "Aviasales · discovery",
  anthropic: "Claude · drafting",
  telegram: "Telegram · posting",
  resend: "Resend · email",
};

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

const ALARM = new Set(["payment_walled", "dark", "auth_failed"]);
const WARN = new Set(["quota_low", "degraded"]);

type Tone = "good" | "warn" | "bad" | "act" | "neutral";

const TONE: Record<Tone, { dot: string; value: string; border: string }> = {
  good: { dot: "bg-good", value: "text-good", border: "border-border" },
  warn: { dot: "bg-amber", value: "text-amber", border: "border-amber/40" },
  bad: { dot: "bg-red", value: "text-red", border: "border-red/50" },
  act: { dot: "bg-signature", value: "text-signature", border: "border-signature-dim" },
  neutral: { dot: "bg-hint", value: "text-text-bright", border: "border-border" },
};

function verdictTone(verdict: string): Tone {
  if (ALARM.has(verdict)) return "bad";
  if (WARN.has(verdict)) return "warn";
  if (verdict === "live") return "good";
  return "neutral";
}

function budgetTone(pct: number): Tone {
  return pct > 0.4 ? "good" : pct > 0.15 ? "warn" : "bad";
}

// ---------------------------------------------------------------------------
// Status tiles
// ---------------------------------------------------------------------------

function Tile({
  label, value, detail, tone, href,
}: {
  label: string; value: string; detail: string; tone: Tone; href: string;
}) {
  const s = TONE[tone];
  return (
    <a
      href={href}
      className={`block rounded-card border bg-bg2 p-4 hover:border-border-bright ${s.border}`}
    >
      <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-text-mid">
        <span className={`h-2 w-2 rounded-full ${s.dot}`} />
        {label}
      </div>
      <div className={`mt-1 font-mono text-2xl font-semibold ${s.value}`}>{value}</div>
      <div className="mt-1 font-mono text-[11px] leading-4 text-hint">{detail}</div>
    </a>
  );
}

/** Budget snapshots older than this are ignored by the tile: a retired
 *  source's last snapshot (e.g. Kiwi at 0) would otherwise keep the
 *  header red forever. The full table still lists it. */
const BUDGET_TILE_MAX_AGE_DAYS = 30;

export function StatusTiles({
  dealCounts, lastScan, health, quotas,
}: {
  dealCounts: DealCounts;
  lastScan: ScanRun | null;
  health: SourceHealthCard[];
  quotas: QuotaCard[];
}) {
  // 1. Deals waiting for the approve tap.
  const deals: Tone = dealCounts.queued > 0 ? "act" : "good";

  // 2. Tracker's last scan: age + outcome.
  let scanTone: Tone = "bad";
  let scanValue = "none";
  let scanDetail = "no scans recorded";
  if (lastScan) {
    const at = lastScan.finishedAt ?? lastScan.startedAt;
    const d = ageDays(at);
    const f = freshness(at);
    scanValue = d === 0 ? "today" : `${d}d ago`;
    scanDetail = `${lastScan.status} · ${lastScan.trigger} · ${lastScan.rowsStored} prices stored`;
    scanTone =
      lastScan.status !== "ok" && lastScan.status !== "degraded"
        ? "bad"
        : f === "stale" ? "bad"
          : f === "aging" || lastScan.status === "degraded" ? "warn"
            : "good";
  }

  // 3. Sources in alarm (from the latest batch run's health verdicts).
  const alarms = health.filter((h) => ALARM.has(h.verdict));
  const warns = health.filter((h) => WARN.has(h.verdict));
  const srcTone: Tone =
    health.length === 0 ? "neutral" : alarms.length ? "bad" : warns.length ? "warn" : "good";
  const srcDetail =
    health.length === 0
      ? "no health data yet — appears after the next batch run"
      : alarms.length
        ? alarms.map((h) => `${sourceLabel(h.source)} (${h.verdict.replace(/_/g, " ")})`).join(", ")
        : warns.length
          ? `none down · ${warns.length} degraded or low: ${warns.map((h) => sourceLabel(h.source)).join(", ")}`
          // Not "all N answering": idle and disabled sources make no calls.
          : "none down or low";

  // 4. The scarcest recently-checked budget.
  const recent = quotas
    .filter((q) => q.remaining !== null && q.limitTotal)
    .filter((q) => ageDays(q.checkedAt) <= BUDGET_TILE_MAX_AGE_DAYS)
    .map((q) => ({ q, pct: (q.remaining as number) / (q.limitTotal as number) }))
    .sort((a, b) => a.pct - b.pct);
  const low = recent[0];

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Tile
        label="Deals to review"
        value={String(dealCounts.queued)}
        detail={`${dealCounts.approvedUnpublished} approved, not sent · ${dealCounts.publishedLast7} published in 7d`}
        tone={deals}
        href="#vuelazo"
      />
      <Tile
        label="Tracker last scan"
        value={scanValue}
        detail={scanDetail}
        tone={scanTone}
        href="#scan-history"
      />
      <Tile
        label="Sources in alarm"
        value={health.length === 0 ? "—" : String(alarms.length)}
        detail={srcDetail}
        tone={srcTone}
        href="#tracker-sources"
      />
      <Tile
        label="Lowest budget"
        value={low ? `${Math.round(low.pct * 100)}%` : "—"}
        detail={
          low
            ? `${sourceLabel(low.q.source)}${isVuelazoSource(low.q.source) ? " (Vuelazo)" : " (tracker)"} · ${low.q.remaining}/${low.q.limitTotal} left` +
              (low.q.resetsAt ? ` · resets ${low.q.resetsAt.slice(0, 10)}` : "")
            : `no budget checked in the last ${BUDGET_TILE_MAX_AGE_DAYS} days`
        }
        tone={low ? budgetTone(low.pct) : "neutral"}
        href="#tracker-sources"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Merged sources table (was three panels: spend, health, budgets)
// ---------------------------------------------------------------------------

interface SourceRow {
  source: string;
  spend?: SpendRow;
  health?: SourceHealthCard;
  quota?: QuotaCard;
}

export function mergeSources(
  spend: SpendRow[], health: SourceHealthCard[], quotas: QuotaCard[],
): SourceRow[] {
  const by = new Map<string, SourceRow>();
  const row = (s: string) => {
    let r = by.get(s);
    if (!r) by.set(s, (r = { source: s }));
    return r;
  };
  for (const s of spend) row(s.source).spend = s;
  for (const h of health) row(h.source).health = h;
  for (const q of quotas) row(q.source).quota = q;
  // Busiest first; sources with no calls keep a stable alphabetical order.
  return [...by.values()].sort(
    (a, b) =>
      (b.spend?.calls ?? 0) - (a.spend?.calls ?? 0) ||
      a.source.localeCompare(b.source),
  );
}

export function SourcesTable({ rows }: { rows: SourceRow[] }) {
  if (rows.length === 0) {
    return (
      <p className="font-mono text-sm text-text-mid">
        No calls, health checks or budget snapshots for these sources yet.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse font-mono text-[12px]">
        <thead>
          <tr className="border-b border-border-bright text-left text-[10px] uppercase tracking-wider text-hint">
            <th className="py-2 pr-4">Source</th>
            <th className="py-2 pr-4">Status</th>
            <th className="py-2 pr-4">Budget left</th>
            <th className="py-2 pr-4">Calls 30d</th>
            <th className="py-2 pr-4">Failed</th>
            <th className="py-2">Last OK</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ source, spend, health, quota }) => {
            const tone = health ? verdictTone(health.verdict) : "neutral";
            const pct =
              quota && quota.remaining !== null && quota.limitTotal
                ? quota.remaining / quota.limitTotal
                : null;
            return (
              <tr key={source} className="border-b border-border align-top">
                <td className="py-2 pr-4">
                  <div className="text-text-bright">{sourceLabel(source)}</div>
                  <div className="text-[10px] text-hint">{source}</div>
                </td>
                <td className="max-w-[260px] py-2 pr-4">
                  {health ? (
                    <>
                      <span className="inline-flex items-center gap-1.5">
                        <span className={`h-2 w-2 rounded-full ${TONE[tone].dot}`} />
                        <span className={tone === "bad" ? "text-red" : tone === "warn" ? "text-amber" : "text-text"}>
                          {health.verdict.replace(/_/g, " ")}
                        </span>
                      </span>
                      {health.detail && (
                        <div className="mt-0.5 text-[10px] leading-4 text-hint">{health.detail}</div>
                      )}
                    </>
                  ) : (
                    <span className="text-hint">—</span>
                  )}
                </td>
                <td className="py-2 pr-4">
                  {quota && quota.remaining !== null ? (
                    <>
                      <span className="text-text-bright">{quota.remaining}</span>
                      <span className="text-hint"> / {quota.limitTotal ?? "?"}</span>
                      {pct !== null && (
                        <div className="mt-1 h-1 w-24 rounded-card bg-bg3">
                          <div
                            className={`h-1 rounded-card ${TONE[budgetTone(pct)].dot}`}
                            style={{ width: `${Math.round(pct * 100)}%` }}
                          />
                        </div>
                      )}
                      <div className="mt-0.5 text-[10px] text-hint">
                        checked {ageDays(quota.checkedAt)}d ago
                        {quota.resetsAt && ` · resets ${quota.resetsAt.slice(0, 10)}`}
                      </div>
                    </>
                  ) : (
                    <span className="text-hint">—</span>
                  )}
                </td>
                <td
                  className="py-2 pr-4 text-text"
                  title={spend ? `${spend.ok} ok · ${spend.empty} empty · ${spend.failed} failed` : undefined}
                >
                  {spend?.calls ?? 0}
                </td>
                <td className={`py-2 pr-4 ${spend && spend.failed > 0 ? "text-red" : "text-hint"}`}>
                  {spend?.failed ?? 0}
                </td>
                <td className="py-2 text-hint">
                  {health?.lastOkAt ? health.lastOkAt.slice(0, 10) : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
