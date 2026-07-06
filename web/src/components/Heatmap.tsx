"use client";

import type { EChartsOption } from "echarts";
import { EChart, chartTheme as t } from "./EChart";
import type { HeatmapCell } from "@/lib/types";

/** Departure-date × stay-days price grid — the signature view: both trip
 *  ends float, bound by stay length. Cheap = green (matrix), expensive =
 *  red. Mirrors the Streamlit Altair heatmap fed by
 *  ui/_common.latest_grid_for_heatmap. */
export function Heatmap({
  cells,
  currency,
}: {
  cells: HeatmapCell[];
  currency: string;
}) {
  if (cells.length === 0) {
    return (
      <p className="font-mono text-sm text-fg-mid">
        No grid data inside the window yet for this origin.
      </p>
    );
  }
  const dates = [...new Set(cells.map((c) => c.departureDate))].sort();
  const stays = [...new Set(cells.map((c) => c.stayDays))].sort(
    (a, b) => a - b,
  );
  const prices = cells.map((c) => c.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);

  const option: EChartsOption = {
    animation: false,
    grid: { left: 44, right: 12, top: 8, bottom: 70 },
    tooltip: {
      backgroundColor: t.bg2,
      borderColor: t.lineBright,
      textStyle: { color: t.fg, fontFamily: t.mono, fontSize: 12 },
      formatter: (p) => {
        const v = (p as unknown as { value: [string, number, number] }).value;
        return `dep <b>${v[0]}</b> · stay <b>${v[1]}d</b><br/>from <b style="color:${t.matrix}">${v[2]} ${currency}</b>`;
      },
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLabel: {
        color: t.fgMid,
        fontFamily: t.mono,
        fontSize: 10,
        rotate: 60,
        formatter: (d: string) => d.slice(5), // MM-DD
      },
      axisLine: { lineStyle: { color: t.line } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "category",
      data: stays.map(String),
      name: "stay (days)",
      nameLocation: "middle",
      nameGap: 32,
      nameTextStyle: { color: t.fgDim, fontFamily: t.mono, fontSize: 10 },
      axisLabel: { color: t.fgMid, fontFamily: t.mono, fontSize: 10 },
      axisLine: { lineStyle: { color: t.line } },
      axisTick: { show: false },
    },
    visualMap: {
      min,
      max,
      calculable: false,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      textStyle: { color: t.fgMid, fontFamily: t.mono, fontSize: 10 },
      inRange: {
        // cheap -> expensive: matrix green, cyan, amber, red
        color: [t.matrix, t.cyan, t.amber, t.danger],
      },
    },
    series: [
      {
        type: "heatmap",
        data: cells.map((c) => [c.departureDate, String(c.stayDays), c.price]),
        emphasis: { itemStyle: { borderColor: t.fg, borderWidth: 1 } },
        itemStyle: { borderColor: "#0a0b10", borderWidth: 1 },
      },
    ],
  };

  // Wide grids scroll inside their own container; sticky page never
  // scrolls horizontally.
  const minWidth = Math.max(560, dates.length * 18 + 80);
  return (
    <div className="overflow-x-auto">
      <div style={{ minWidth }}>
        <EChart option={option} height={Math.max(220, stays.length * 12 + 120)} />
      </div>
    </div>
  );
}
