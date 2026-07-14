import { redirect } from "next/navigation";
import { ConfigEditor } from "@/components/ops/ConfigEditor";
import { CredentialsAdmin } from "@/components/ops/CredentialsAdmin";
import { TriggerScan } from "@/components/ops/TriggerScan";
import { UserAdmin } from "@/components/ops/UserAdmin";
import { Card, SectionHeading } from "@/components/Section";
import { isOpsBreakGlass } from "@/lib/auth";
import { ageDays } from "@/lib/format";
import { getQuotas, getScanHistory, getSourceHealth, getSpend } from "@/lib/queries";
import { requireUser } from "@/lib/users";
import type { RouteConfigJson } from "@/lib/config-schema";
import { db } from "@/lib/db";

// Operators always see live state — never a cached render.
export const dynamic = "force-dynamic";

const ROUTE_ID = "spain-nairobi";

const QUOTA_LABELS: Record<string, string> = {
  serpapi: "SerpAPI (verify)",
  kiwi: "Kiwi (discovery)",
  aviasales: "Aviasales (bonus)",
  skyscanner: "Sky Scrapper (curve)",
  searchapi: "SearchAPI (break-glass)",
};

export default async function OpsPage() {
  // Owner-only: the proxy admits any valid session (stateless); the
  // role check happens here where the DB is available. Break-glass
  // APP_PASSWORD sessions administer too (owner bootstrap path).
  const owner = await requireUser("owner");
  if (!owner && !(await isOpsBreakGlass())) redirect("/ops/login");

  const [quotas, scans, health, spend, cfgRow] = await Promise.all([
    getQuotas(),
    getScanHistory(ROUTE_ID, 10),
    getSourceHealth(),
    getSpend(30),
    db().execute({
      sql: "SELECT config_json FROM routes WHERE route_id = ?",
      args: [ROUTE_ID],
    }),
  ]);
  const config = cfgRow.rows[0]
    ? (JSON.parse(String(cfgRow.rows[0]["config_json"])) as RouteConfigJson)
    : null;

  return (
    <div className="space-y-10">
      <h1 className="font-mono text-lg text-text-bright">
        OPS · {ROUTE_ID}
      </h1>

      <section>
        <SectionHeading>Run a scan</SectionHeading>
        <TriggerScan />
      </section>

      <section>
        <SectionHeading>Users & invites</SectionHeading>
        <Card>
          <UserAdmin />
        </Card>
      </section>

      <section>
        <SectionHeading>API keys</SectionHeading>
        <CredentialsAdmin />
      </section>

      <section>
        <SectionHeading>Search settings</SectionHeading>
        <Card>
          {config ? (
            <ConfigEditor initial={config} />
          ) : (
            <p className="font-mono text-sm text-text-mid">
              No config row in the DB yet — run one scan first.
            </p>
          )}
        </Card>
      </section>

      <section>
        <SectionHeading>Spend · last 30 days</SectionHeading>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-mono text-[13px]">
            <thead>
              <tr className="border-b border-line-bright text-left text-[10px] uppercase tracking-wider text-fg-dim">
                <th className="py-2 pr-4">source</th>
                <th className="py-2 pr-4">calls</th>
                <th className="py-2 pr-4">ok</th>
                <th className="py-2 pr-4">empty</th>
                <th className="py-2 pr-4">failed</th>
              </tr>
            </thead>
            <tbody>
              {spend.map((s) => (
                <tr key={s.source} className="border-b border-line">
                  <td className="py-2 pr-4 text-fg-bright">
                    {QUOTA_LABELS[s.source] ?? s.source}
                  </td>
                  <td className="py-2 pr-4">{s.calls}</td>
                  <td className="py-2 pr-4 text-good">{s.ok}</td>
                  <td className="py-2 pr-4 text-fg-dim">{s.empty}</td>
                  <td className={`py-2 pr-4 ${s.failed > 0 ? "text-red" : "text-fg-dim"}`}>
                    {s.failed}
                  </td>
                </tr>
              ))}
              {spend.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-2 font-mono text-sm text-fg-mid">
                    No API calls recorded in the last 30 days.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="mt-2 font-mono text-[10px] text-hint">
          One row per HTTP attempt (spend_events). "failed" = errors +
          rate-limits + payment walls — the never-silent signal.
        </p>
      </section>

      <section>
        <SectionHeading>Source health</SectionHeading>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {health.map((h) => {
            const alarm =
              h.verdict === "payment_walled" ||
              h.verdict === "dark" ||
              h.verdict === "auth_failed";
            const dotColor = alarm
              ? "bg-red"
              : h.verdict === "quota_low" || h.verdict === "degraded"
                ? "bg-amber"
                : h.verdict === "live"
                  ? "bg-good"
                  : "bg-hint";
            return (
              <Card key={h.source}>
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11px] uppercase tracking-wider text-text-mid">
                    {QUOTA_LABELS[h.source] ?? h.source}
                  </span>
                  <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tracking-wider">
                    <span className={`h-2 w-2 rounded-full ${dotColor}`} />
                    {h.verdict.replace(/_/g, " ")}
                  </span>
                </div>
                <div className="mt-2 font-mono text-[11px] text-hint">
                  {h.detail || "—"}
                </div>
                <div className="mt-1 font-mono text-[10px] text-hint">
                  {h.attempts} calls · {h.stored} rows
                  {h.lastOkAt && ` · last ok ${h.lastOkAt.slice(0, 10)}`}
                </div>
              </Card>
            );
          })}
          {health.length === 0 && (
            <p className="font-mono text-sm text-text-mid">
              No source health yet — it appears after the next batch run.
            </p>
          )}
        </div>
      </section>

      <section>
        <SectionHeading>API budgets</SectionHeading>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {quotas.map((q) => {
            const pct =
              q.remaining !== null && q.limitTotal
                ? q.remaining / q.limitTotal
                : null;
            const barColor =
              pct === null
                ? "bg-hint"
                : pct > 0.4
                  ? "bg-good"
                  : pct > 0.15
                    ? "bg-amber"
                    : "bg-red";
            return (
              <Card key={q.source}>
                <div className="font-mono text-[11px] uppercase tracking-wider text-text-mid">
                  {QUOTA_LABELS[q.source] ?? q.source}
                </div>
                <div className="mt-1 font-mono text-2xl text-text-bright">
                  {q.remaining ?? "?"}
                  <span className="text-sm text-hint">
                    {" "}
                    / {q.limitTotal ?? "?"}
                  </span>
                </div>
                {pct !== null && (
                  <div className="mt-2 h-1 w-full rounded-card bg-bg3">
                    <div
                      className={`h-1 rounded-card ${barColor}`}
                      style={{ width: `${Math.round(pct * 100)}%` }}
                    />
                  </div>
                )}
                <div className="mt-2 font-mono text-[10px] text-hint">
                  checked {ageDays(q.checkedAt)}d ago
                  {q.resetsAt &&
                    ` · resets ${q.resetsAt.slice(0, 10)}`}
                </div>
              </Card>
            );
          })}
          {quotas.length === 0 && (
            <p className="font-mono text-sm text-text-mid">
              No quota snapshots yet — they refresh as scans run.
            </p>
          )}
        </div>
      </section>

      <section>
        <SectionHeading>Scan history</SectionHeading>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-mono text-[12px]">
            <thead>
              <tr className="border-b border-border-bright text-left text-[10px] uppercase tracking-wider text-hint">
                <th className="py-2 pr-4">Started</th>
                <th className="py-2 pr-4">Trigger</th>
                <th className="py-2 pr-4">Sources</th>
                <th className="py-2 pr-4">Rows</th>
                <th className="py-2 pr-4">Alerts</th>
                <th className="py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {scans.map((s) => (
                <tr key={s.startedAt} className="border-b border-border">
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {s.startedAt.replace("T", " ").replace("Z", "")}
                  </td>
                  <td className="py-2 pr-4">{s.trigger}</td>
                  <td className="max-w-[220px] truncate py-2 pr-4 text-text-mid">
                    {s.sources}
                  </td>
                  <td className="py-2 pr-4">{s.rowsStored}</td>
                  <td className="py-2 pr-4">
                    {s.alertsFired > 0 ? (
                      <span className="text-good">{s.alertsFired}</span>
                    ) : (
                      0
                    )}
                  </td>
                  <td
                    className={`py-2 ${
                      s.status === "ok"
                        ? "text-good"
                        : s.status === "degraded"
                          ? "text-amber"
                          : "text-red"
                    }`}
                  >
                    {s.status}
                  </td>
                </tr>
              ))}
              {scans.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-3 text-text-mid">
                    No scans recorded yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
