"use client";

// The daily 10-minute ritual (D3): <=15 cards, three actions each —
// approve / reject with a one-tap reason / edit-then-approve. Mistake-
// class cards wear the amber badge; the sparkline is the brand's
// "precio normal, demostrado" frame.

import { useState } from "react";
import { useRouter } from "next/navigation";
import { EChart, chartTheme } from "@/components/EChart";
import type { DealRow, SparkPoint } from "@/lib/deals";

const REASONS = [
  "too_common",
  "bad_dates",
  "ulcc_junk",
  "thin_saving",
  "other",
] as const;

const btnPrimary =
  "rounded-card border border-signature-dim bg-bg2 px-4 py-2 font-mono text-[12px] font-semibold tracking-wider text-signature hover:shadow-glow-sig disabled:opacity-40";
const btnRow =
  "rounded-card border border-border-bright px-2.5 py-1 font-mono text-[11px] tracking-wider text-text hover:border-signature-dim disabled:opacity-40";
const btnDanger =
  "rounded-card border border-border px-2.5 py-1 font-mono text-[11px] tracking-wider text-red/80 hover:border-red disabled:opacity-40";

type CardState =
  | { kind: "idle" }
  | { kind: "busy" }
  | { kind: "done"; msg: string }
  | { kind: "error"; msg: string };

function Spark({ points, currency }: { points: SparkPoint[]; currency: string }) {
  if (points.length < 2) {
    return (
      <p className="font-mono text-[11px] text-hint">
        sin histórico suficiente para la gráfica
      </p>
    );
  }
  return (
    <EChart
      height={70}
      option={{
        grid: { left: 4, right: 4, top: 6, bottom: 4 },
        xAxis: { type: "category", show: false, data: points.map((p) => p.day) },
        yAxis: { type: "value", show: false, min: "dataMin" },
        tooltip: {
          trigger: "axis",
          backgroundColor: chartTheme.bg2,
          borderColor: chartTheme.border,
          textStyle: { color: chartTheme.text, fontFamily: chartTheme.mono, fontSize: 11 },
          valueFormatter: (v) => `${v} ${currency}`,
        },
        series: [
          {
            type: "line",
            data: points.map((p) => p.price),
            showSymbol: false,
            smooth: true,
            lineStyle: { color: chartTheme.good, width: 1.5 },
            areaStyle: { color: chartTheme.good, opacity: 0.08 },
          },
        ],
      }}
    />
  );
}

function DealCard({ deal, spark }: { deal: DealRow; spark: SparkPoint[] }) {
  const router = useRouter();
  const [state, setState] = useState<CardState>({ kind: "idle" });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(deal.draftEs ?? "");
  const [reason, setReason] = useState<(typeof REASONS)[number]>("too_common");
  const [freePick, setFreePick] = useState(false);

  async function act(body: Record<string, unknown>, doneMsg: string) {
    setState({ kind: "busy" });
    try {
      const r = await fetch(`/api/ops/deals/${deal.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = (await r.json()) as { error?: string; dispatched?: boolean };
      if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`);
      // Saving an edited draft must NOT dead-end the card: the deal is
      // still queued and the approve/reject ritual continues.
      if (body.action === "edit") {
        setState({ kind: "idle" });
      } else {
        setState({
          kind: "done",
          msg:
            doneMsg +
            (body.action === "approve"
              ? data.dispatched
                ? " · fan-out lanzado"
                : " · saldrá en el próximo run"
              : ""),
        });
      }
      router.refresh();
    } catch (e) {
      setState({ kind: "error", msg: e instanceof Error ? e.message : "error" });
    }
  }

  const verifyQ = encodeURIComponent(
    `vuelos de ${deal.origin} a ${deal.dest}` +
      (deal.sampleDates ? ` el ${deal.sampleDates.split("..")[0]}` : ""),
  );
  const conf = deal.confidence;
  const isApproved = deal.status === "approved";

  return (
    <div className="rounded-panel border border-border bg-bg2 p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-mono text-[15px] font-semibold text-text-bright">
          {deal.origin} → {deal.dest}
        </span>
        <span className="font-mono text-[15px] font-semibold text-good">
          {deal.price} {deal.currency}
        </span>
        {deal.pctBelow != null && (
          <span className="font-mono text-[11px] text-good">
            −{Math.round(deal.pctBelow)}%
          </span>
        )}
        {deal.dealClass === "mistake" && (
          <span className="rounded-card border border-amber px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-amber">
            mistake?
          </span>
        )}
        {isApproved && (
          <span className="rounded-card border border-good px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-good">
            aprobado · pendiente de envío
          </span>
        )}
        <span className="ml-auto font-mono text-[10px] uppercase tracking-wider text-hint">
          #{deal.id} · score {deal.score ?? "—"}
        </span>
      </div>

      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[11px] text-text-mid">
        <span>fechas {deal.sampleDates ?? "—"}</span>
        {deal.baselineMedian != null && <span>normal ~{deal.baselineMedian} {deal.currency}</span>}
        {conf?.level && (
          <span>
            conf {conf.level} ~{conf.score}% ({(conf.families ?? []).join(", ")})
          </span>
        )}
        <a
          className="text-signature hover:underline"
          href={`https://www.google.com/travel/flights?hl=es&q=${verifyQ}`}
          target="_blank"
          rel="noreferrer"
        >
          verificar ↗
        </a>
      </div>

      <div className="mt-3">
        <Spark points={spark} currency={deal.currency} />
      </div>

      {editing ? (
        <textarea
          className="mt-3 h-40 w-full rounded-card border border-border bg-bg px-2.5 py-2 font-mono text-[12px] leading-relaxed text-text-bright outline-none focus:border-signature-dim"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
      ) : (
        <p className="mt-3 whitespace-pre-wrap border-l-2 border-border pl-3 text-[13px] leading-relaxed text-text">
          {deal.draftEs ?? "(sin borrador)"}
        </p>
      )}

      {state.kind === "done" ? (
        <p className="mt-3 font-mono text-[12px] text-good">✓ {state.msg}</p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {!isApproved && (
            <>
              <button
                className={btnPrimary}
                disabled={state.kind === "busy"}
                onClick={() =>
                  void act(
                    {
                      action: "approve",
                      free_pick: freePick,
                      ...(editing && draft.trim() ? { draft_es: draft } : {}),
                    },
                    "aprobado",
                  )
                }
              >
                APROBAR
              </button>
              <label className="flex items-center gap-1.5 font-mono text-[11px] text-text-mid">
                <input
                  type="checkbox"
                  checked={freePick}
                  onChange={(e) => setFreePick(e.target.checked)}
                />
                free pick (canal público T+24h)
              </label>
              <button
                className={btnRow}
                disabled={state.kind === "busy"}
                onClick={() => {
                  if (editing && draft.trim() && draft !== deal.draftEs) {
                    void act({ action: "edit", draft_es: draft }, "borrador guardado");
                  }
                  setEditing(!editing);
                }}
              >
                {editing ? "GUARDAR TEXTO" : "EDITAR"}
              </button>
              <span className="ml-2 flex items-center gap-1.5">
                <select
                  className="rounded-card border border-border bg-bg px-1.5 py-1 font-mono text-[11px] text-text"
                  value={reason}
                  onChange={(e) =>
                    setReason(e.target.value as (typeof REASONS)[number])
                  }
                >
                  {REASONS.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
                <button
                  className={btnDanger}
                  disabled={state.kind === "busy"}
                  onClick={() => void act({ action: "reject", reason }, "rechazado")}
                >
                  RECHAZAR
                </button>
              </span>
            </>
          )}
          {state.kind === "error" && (
            <span className="font-mono text-[12px] text-red">{state.msg}</span>
          )}
        </div>
      )}
    </div>
  );
}

export default function DealQueue({
  deals,
  sparks,
}: {
  deals: DealRow[];
  sparks: Record<number, SparkPoint[]>;
}) {
  if (deals.length === 0) {
    return (
      <p className="font-mono text-sm text-text-mid">
        cola vacía — el próximo run del pipeline la llenará si hay chollos.
      </p>
    );
  }
  return (
    <div className="space-y-4">
      {deals.map((d) => (
        <DealCard key={d.id} deal={d} spark={sparks[d.id] ?? []} />
      ))}
    </div>
  );
}
