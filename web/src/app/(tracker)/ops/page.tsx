import { redirect } from "next/navigation";
import { ConfigEditor } from "@/components/ops/ConfigEditor";
import { CredentialsAdmin } from "@/components/ops/CredentialsAdmin";
import DealQueue from "@/components/ops/DealQueue";
import {
  SourcesTable,
  StatusTiles,
  isVuelazoSource,
  mergeSources,
} from "@/components/ops/OpsStatus";
import { TriggerScan } from "@/components/ops/TriggerScan";
import { UserAdmin } from "@/components/ops/UserAdmin";
import { Card, SectionHeading } from "@/components/Section";
import { isOpsBreakGlass } from "@/lib/auth";
import { getDealCounts, getDealQueue, getRouteSparkline, type SparkPoint } from "@/lib/deals";
import { getConfidence, getQuotas, getScanHistory, getSourceHealth, getSpend } from "@/lib/queries";
import { requireUser } from "@/lib/users";
import type { RouteConfigJson } from "@/lib/config-schema";
import { db } from "@/lib/db";

// Operators always see live state — never a cached render.
export const dynamic = "force-dynamic";

const ROUTE_ID = "spain-nairobi";

const CONFIDENCE_TONE: Record<string, string> = {
  high: "text-good",
  medium: "text-cyan99",
  low: "text-amber",
};

const SCAN_STATUS_TONE: Record<string, string> = {
  ok: "text-good",
  degraded: "text-amber",
};

/** A project group: one title + one line on what lives in it. */
function Group({
  id, title, hint, children,
}: {
  id: string; title: string; hint: string; children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-14 space-y-8">
      <div>
        <h2 className="font-mono text-base font-semibold tracking-wider text-text-bright">
          {title}
        </h2>
        <p className="mt-1 font-sans text-[13px] text-text-mid">{hint}</p>
      </div>
      {children}
    </section>
  );
}

const NAV = [
  { href: "#vuelazo", label: "VUELAZO" },
  { href: "#tracker", label: "NAIROBI TRACKER" },
  { href: "#admin", label: "ADMIN" },
];

/** Operator console. Top: four tiles that answer "do I need to act?".
 *  Below, everything grouped by the project it belongs to — Vuelazo
 *  (deals + its paid services), the Nairobi tracker (its one search),
 *  and admin (people + keys) — instead of ten equal-weight panels. */
export default async function OpsPage() {
  // Owner-only: the proxy admits any valid session (stateless); the
  // role check happens here where the DB is available. Break-glass
  // APP_PASSWORD sessions administer too (owner bootstrap path).
  const owner = await requireUser("owner");
  if (!owner && !(await isOpsBreakGlass())) redirect("/ops/login");

  const [quotas, scans, health, spend, confidence, cfgRow, dealQueue, dealCounts] =
    await Promise.all([
      getQuotas(),
      getScanHistory(ROUTE_ID, 10),
      getSourceHealth(),
      getSpend(30),
      getConfidence(),
      db().execute({
        sql: "SELECT config_json FROM routes WHERE route_id = ?",
        args: [ROUTE_ID],
      }),
      getDealQueue(),
      getDealCounts(),
    ]);
  const config = cfgRow.rows[0]
    ? (JSON.parse(String(cfgRow.rows[0]["config_json"])) as RouteConfigJson)
    : null;
  const sparkEntries = await Promise.all(
    dealQueue.map(async (d) => [d.id, await getRouteSparkline(d.origin, d.dest)] as const),
  );
  const sparks: Record<number, SparkPoint[]> = Object.fromEntries(sparkEntries);

  const sources = mergeSources(spend, health, quotas);
  const vuelazoSources = sources.filter((s) => isVuelazoSource(s.source));
  const trackerSources = sources.filter((s) => !isVuelazoSource(s.source));

  return (
    <div className="space-y-10">
      <div className="space-y-4">
        <h1 className="font-mono text-lg text-text-bright">OPS</h1>
        <StatusTiles
          dealCounts={dealCounts}
          lastScan={scans[0] ?? null}
          health={health}
          quotas={quotas}
        />
      </div>

      <nav className="sticky top-0 z-10 -mx-4 flex gap-5 border-b border-border bg-bg/95 px-4 py-2.5 font-mono text-xs tracking-wider text-text-mid backdrop-blur">
        {NAV.map((l) => (
          <a key={l.href} href={l.href} className="hover:text-signature">
            {l.label}
          </a>
        ))}
      </nav>

      <Group
        id="vuelazo"
        title="Vuelazo"
        hint="Deals waiting for your approval, and the services Vuelazo pays for."
      >
        <section>
          <SectionHeading as="h3">Deal queue</SectionHeading>
          <p className="mb-3 font-mono text-[11px] text-hint">
            {dealCounts.queued} en cola · {dealCounts.approvedUnpublished} aprobados
            sin enviar · {dealCounts.publishedLast7} publicados (7d) ·{" "}
            {dealCounts.rejectedLast7} rechazados (7d)
          </p>
          <DealQueue deals={dealQueue} sparks={sparks} />
        </section>

        <section>
          <SectionHeading as="h3" id="vuelazo-sources">
            Vuelazo sources
          </SectionHeading>
          <SourcesTable rows={vuelazoSources} />
        </section>
      </Group>

      <Group
        id="tracker"
        title={`Nairobi tracker · ${ROUTE_ID}`}
        hint="The MAD/BCN → NBO search: run it, change its window, and check the sources it uses."
      >
        <section>
          <SectionHeading as="h3">Run a scan</SectionHeading>
          <TriggerScan />
        </section>

        <section>
          <SectionHeading as="h3">Search settings</SectionHeading>
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
          <SectionHeading as="h3" id="tracker-sources">
            Tracker sources
          </SectionHeading>
          <SourcesTable rows={trackerSources} />
          <p className="mt-2 font-mono text-[10px] text-hint">
            Calls = HTTP attempts in the last 30 days (spend_events). Failed =
            errors, rate limits and payment walls. Status comes from the latest
            batch run; budgets from the latest quota snapshot.
          </p>
        </section>

        <section>
          <SectionHeading as="h3">Best-price confidence</SectionHeading>
          {confidence ? (
            <Card>
              <div className="flex items-baseline gap-3">
                <span
                  className={`font-mono text-2xl font-semibold ${
                    CONFIDENCE_TONE[confidence.level] ?? "text-hint"
                  }`}
                >
                  {confidence.level.toUpperCase()}
                </span>
                <span className="font-mono text-[13px] text-text-mid">
                  ~{confidence.score}% · {confidence.families.join(" + ") || "no data"}
                </span>
              </div>
              <p className="mt-2 font-mono text-[12px] leading-5 text-text-mid">
                {confidence.note}
              </p>
              <p className="mt-2 font-mono text-[10px] text-hint">
                Counts INDEPENDENT coverage families (Google, OTA-metasearch,
                cached), not endpoints — two Google mirrors are one family.
              </p>
            </Card>
          ) : (
            <p className="font-mono text-sm text-text-mid">
              No confidence score yet — it appears after the next batch run.
            </p>
          )}
        </section>

        <section>
          <SectionHeading as="h3" id="scan-history">
            Scan history
          </SectionHeading>
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
                    <td className="whitespace-nowrap py-2 pr-4">
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
                    <td className={`py-2 ${SCAN_STATUS_TONE[s.status] ?? "text-red"}`}>
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
      </Group>

      <Group
        id="admin"
        title="Admin"
        hint="Who can log in, and the API keys the scanners use."
      >
        <section>
          <SectionHeading as="h3">Users & invites</SectionHeading>
          <Card>
            <UserAdmin />
          </Card>
        </section>

        <section>
          <SectionHeading as="h3">API keys</SectionHeading>
          <CredentialsAdmin />
        </section>
      </Group>
    </div>
  );
}
