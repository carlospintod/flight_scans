import Link from "next/link";
import { redirect } from "next/navigation";
import { SearchControls } from "@/components/SearchControls";
import { Card, SectionHeading } from "@/components/Section";
import { getUserSession } from "@/lib/auth";
import { fmtDate, seenLabel } from "@/lib/format";
import { getUserSearches } from "@/lib/queries";

export const dynamic = "force-dynamic";

/** The authed home: your searches, each with its latest run digest —
 *  what ran, what it found, exactly what it spent vs what was promised,
 *  and WHY anything was skipped (skip-and-notify made visible). */
export default async function SearchesPage() {
  const session = await getUserSession();
  if (!session) redirect("/join");
  const searches = await getUserSearches(session.userId);

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-mono text-lg text-fg-bright">YOUR SEARCHES</h1>
        <Link
          href="/searches/new"
          className="rounded-card border border-matrix-dim bg-bg-2 px-4 py-2 font-mono text-[13px] font-semibold tracking-wider text-matrix hover:shadow-glow"
        >
          + NEW SEARCH
        </Link>
      </div>

      {searches.length === 0 && (
        <Card>
          <p className="font-mono text-sm text-fg-mid">
            No searches yet — create your first one. You set the route, the
            date window, and how long you want to stay; the tracker hunts
            the cheapest combination three times a week.
          </p>
        </Card>
      )}

      {searches.map((s) => (
        <Card key={s.searchId}>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div className="font-mono text-sm text-fg-bright">
              {s.window
                ? `${s.window.origins.join("/")} → ${s.window.destinations.join("/")}`
                : s.searchId}
              <span
                className={`ml-3 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${
                  s.status === "active"
                    ? "border-matrix-dim text-matrix"
                    : "border-fg-dim text-fg-dim"
                }`}
              >
                {s.status}
              </span>
              {s.isPublic && (
                <span className="ml-2 rounded border border-cyan px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-cyan">
                  public demo
                </span>
              )}
            </div>
            <Link
              href={`/s/${s.searchId}`}
              className="font-mono text-[12px] text-matrix-dim hover:text-matrix"
            >
              view results →
            </Link>
          </div>

          {s.window && (
            <p className="mt-1 font-mono text-[12px] text-fg-mid">
              {fmtDate(s.window.earliestDeparture)} –{" "}
              {fmtDate(s.window.latestReturn)} · {s.window.minStay}–
              {s.window.maxStay} day stays
            </p>
          )}

          {s.cheapestNow && (
            <p className="mt-3 font-mono text-[13px]">
              <span className="text-fg-dim">cheapest now </span>
              <span className="text-lg font-semibold text-matrix">
                {s.cheapestNow.price} {s.cheapestNow.currency}
              </span>
              <span className="text-fg">
                {" "}· {s.cheapestNow.origin} {fmtDate(s.cheapestNow.departureDate)}
                {" → "}{fmtDate(s.cheapestNow.returnDate)}
              </span>
            </p>
          )}

          <div className="mt-3 border-t border-line pt-3">
            {s.lastRun ? (
              <div className="font-mono text-[12px]">
                <span className="text-fg-dim">last run </span>
                <span className="text-fg">{seenLabel(s.lastRun.startedAt).replace("seen ", "")}</span>
                {s.lastRun.status === "skipped" ? (
                  <span className="ml-2 text-amber">
                    SKIPPED ({s.lastRun.skipReason ?? "capacity"}) — budget
                    intact, first in line next run
                  </span>
                ) : (
                  <>
                    <span
                      className={`ml-2 ${
                        s.lastRun.status === "ok"
                          ? "text-matrix-dim"
                          : "text-amber"
                      }`}
                    >
                      {s.lastRun.status.toUpperCase()}
                    </span>
                    <span className="text-fg-mid">
                      {" "}· {s.lastRun.rowsStored} prices ·{" "}
                      {s.lastRun.alertsFired} alerts
                    </span>
                    {s.lastRun.reservedVsUsed.length > 0 && (
                      <div className="mt-1 text-[11px] text-fg-dim">
                        {s.lastRun.reservedVsUsed.map((r) => (
                          <span key={`${r.source}${r.kind}`} className="mr-3">
                            {r.source}
                            {r.kind === "contingency" ? "*" : ""} ≤{r.reserved}
                            →{r.used}
                            {r.state === "released" ? " (unused)" : ""}
                          </span>
                        ))}
                        <span className="text-fg-dim">
                          * contingency — reserved, only spent if the primary
                          rail fails
                        </span>
                      </div>
                    )}
                  </>
                )}
              </div>
            ) : (
              <p className="font-mono text-[12px] text-fg-dim">
                no runs yet — next scheduled window is Mon/Wed/Sat morning
              </p>
            )}
          </div>

          <div className="mt-3 border-t border-line pt-3">
            <SearchControls
              searchId={s.searchId}
              status={s.status}
              notify={s.notify}
            />
          </div>
        </Card>
      ))}

      <p className="font-mono text-[11px] text-fg-dim">
        Scans run Mon/Wed/Sat ~05:23 UTC (best-effort — GitHub occasionally
        delays them). Every number shown as ≤N is a guaranteed upper bound:
        a run can spend less, never more.
      </p>
    </div>
  );
}
