"use client";

import type { EChartsOption } from "echarts";
import { EChart, chartTheme as t } from "./EChart";
import { fmtDate } from "@/lib/format";
import type { HeatmapCell, RouteWindow } from "@/lib/types";
import { departureDays, fitsWindow, stayLengths } from "@/lib/window";

/** Departure-date × stay-days price grid for one origin. The axes span
 *  the WHOLE search window, so a date pair nobody has priced yet is a
 *  visible empty square instead of being silently left out (with only a
 *  few priced pairs, the old data-only axes stretched them into one big
 *  rectangle). Cheap = green → red, on a colour scale clamped at
 *  `ceiling`; pricier cells share one neutral colour so a single outlier
 *  can't wash out the scale. */

const CELL_W = 14;
const CELL_H = 10;
const GRID = { left: 44, right: 12, top: 8, bottom: 30 } as const;
const SCALE = [t.good, t.cyan, t.amber, t.red];
const EMPTY = t.border;   // never priced (inside the window)
const PRICEY = t.hint;    // priced, above the ceiling

// [departure, stay, price]. Unpriced cells carry 0: ECharts skips heatmap
// items whose value is null/NaN, and those squares must still be drawn.
type Point = [string, string, number];
const NOT_PRICED_SERIES = 0;

const CELL_STYLE = {
  itemStyle: { borderColor: t.bg2, borderWidth: 1 },
  emphasis: { itemStyle: { borderColor: t.text, borderWidth: 1 } },
};

/** A hidden one-colour visualMap for one series (values in [min, max]). */
function solidMap(seriesIndex: number, color: string, min: number, max: number) {
  return {
    show: false, seriesIndex, dimension: 2, min, max,
    inRange: { color: [color, color] },
  };
}

function returnDate(departure: string, stay: number): string {
  const ms = Date.parse(departure + "T00:00:00Z") + stay * 86_400_000;
  return new Date(ms).toISOString().slice(0, 10);
}

export function Heatmap({
  cells,
  currency,
  window: w,
  ceiling,
}: {
  cells: HeatmapCell[];
  currency: string;
  window: RouteWindow;
  ceiling: number;
}) {
  const dates = departureDays(w);
  const stays = stayLengths(w);
  if (dates.length === 0 || stays.length === 0) {
    return (
      <p className="font-mono text-sm text-text-mid">
        This window has no possible trips — check the dates and stay length.
      </p>
    );
  }

  const priced = new Map<string, number>();
  for (const c of cells) priced.set(`${c.departureDate}|${c.stayDays}`, c.price);

  const empty: Point[] = [];
  const cheap: Point[] = [];
  const pricey: Point[] = [];
  for (const d of dates) {
    for (const s of stays) {
      if (!fitsWindow(w, d, s)) continue; // impossible pair: leave blank
      const price = priced.get(`${d}|${s}`);
      if (price === undefined) empty.push([d, String(s), 0]);
      else if (price > ceiling) pricey.push([d, String(s), price]);
      else cheap.push([d, String(s), price]);
    }
  }

  const cheapPrices = cheap.map((p) => p[2]);
  const lo = cheapPrices.length ? Math.min(...cheapPrices) : 0;
  const hi = cheapPrices.length ? Math.max(...cheapPrices) : 0;
  const flat = lo === hi; // one price only: a gradient would be meaningless

  const option: EChartsOption = {
    animation: false,
    grid: { ...GRID },
    tooltip: {
      backgroundColor: t.bg2,
      borderColor: t.borderBright,
      textStyle: { color: t.text, fontFamily: t.mono, fontSize: 12 },
      formatter: (params) => {
        const { value: v, seriesIndex } =
          params as unknown as { value: Point; seriesIndex: number };
        const stay = Number(v[1]);
        const head =
          `leave <b>${fmtDate(v[0])}</b> · back <b>${fmtDate(returnDate(v[0], stay))}</b>` +
          ` · ${stay} days`;
        if (seriesIndex === NOT_PRICED_SERIES) {
          return `${head}<br/><span style="color:${t.hint}">not priced yet</span>`;
        }
        const colour = v[2] > ceiling ? t.textMid : t.good;
        return `${head}<br/><b style="color:${colour}">${v[2]} ${currency}</b>`;
      },
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLabel: {
        color: t.textMid,
        fontFamily: t.mono,
        fontSize: 10,
        interval: (i: number) => i % 7 === 0,
        formatter: (d: string) => fmtDate(d),
      },
      axisLine: { lineStyle: { color: t.border } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "category",
      data: stays.map(String),
      name: "stay (days)",
      nameLocation: "middle",
      nameGap: 30,
      nameTextStyle: { color: t.hint, fontFamily: t.mono, fontSize: 10 },
      axisLabel: {
        color: t.textMid,
        fontFamily: t.mono,
        fontSize: 10,
        interval: (i: number) => i % 5 === 0,
      },
      axisLine: { lineStyle: { color: t.border } },
      axisTick: { show: false },
    },
    // ECharts colours heatmap cells ONLY through a visualMap (a cartesian
    // heatmap series without one throws), so the two solid-colour series
    // get single-colour maps too. A flat cheap series (one price) also
    // gets a solid map: a gradient with min === max washes out.
    visualMap: [
      solidMap(NOT_PRICED_SERIES, EMPTY, 0, 1),
      solidMap(1, PRICEY, ceiling, Math.max(ceiling + 1, ...pricey.map((p) => p[2]))),
      flat
        ? solidMap(2, t.good, lo - 1, lo + 1)
        : { show: false, seriesIndex: 2, dimension: 2, min: lo, max: hi,
            inRange: { color: SCALE } },
    ],
    series: [
      { name: "not priced", type: "heatmap", data: empty, ...CELL_STYLE },
      { name: `over ${ceiling}`, type: "heatmap", data: pricey, ...CELL_STYLE },
      { name: "priced", type: "heatmap", data: cheap, ...CELL_STYLE },
    ],
  };

  // Uniform cells; a wide window scrolls inside its own container so the
  // page itself never scrolls sideways.
  const minWidth = dates.length * CELL_W + GRID.left + GRID.right;
  const height = Math.max(110, stays.length * CELL_H + GRID.top + GRID.bottom);
  return (
    <>
      <div className="overflow-x-auto">
        <div style={{ minWidth }}>
          <EChart option={option} height={height} />
        </div>
      </div>
      <Legend
        lo={lo}
        hi={hi}
        hasCheap={cheap.length > 0}
        hasPricey={pricey.length > 0}
        ceiling={ceiling}
        currency={currency}
      />
    </>
  );
}

function Swatch({ color }: { color: string }) {
  return (
    <span
      className="inline-block h-2.5 w-2.5 rounded-[2px] align-middle"
      style={{ background: color }}
    />
  );
}

function Legend({
  lo, hi, hasCheap, hasPricey, ceiling, currency,
}: {
  lo: number; hi: number; hasCheap: boolean; hasPricey: boolean;
  ceiling: number; currency: string;
}) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 font-mono text-[11px] text-text-mid">
      {hasCheap &&
        (lo === hi ? (
          <span className="inline-flex items-center gap-1.5">
            <Swatch color={t.good} /> {lo} {currency}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5">
            cheaper
            <span
              className="inline-block h-2.5 w-20 rounded-[2px]"
              style={{ background: `linear-gradient(90deg, ${SCALE.join(", ")})` }}
            />
            pricier · {lo}–{hi} {currency}
          </span>
        ))}
      {hasPricey && (
        <span className="inline-flex items-center gap-1.5">
          <Swatch color={PRICEY} /> over {ceiling} {currency}
        </span>
      )}
      <span className="inline-flex items-center gap-1.5">
        <Swatch color={EMPTY} /> not priced yet
      </span>
    </div>
  );
}
