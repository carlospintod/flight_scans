"use client";

import type { EChartsOption } from "echarts";
import { EChart, chartTheme as t } from "./EChart";
import type { HistoryPoint } from "@/lib/types";

const SOURCE_COLORS: Record<string, string> = {
  googleflights: t.matrix,
  serpapi: t.cyan,
  searchapi: t.amber,
  kiwi: "#b57bff",
  aviasales: "#ff8c42",
  skyscanner: t.fgMid,
};

/** Price-over-time per source for one itinerary — mirrors the Streamlit
 *  itinerary_history_chart. */
export function HistoryChart({
  points,
  currency,
}: {
  points: HistoryPoint[];
  currency: string;
}) {
  if (points.length === 0) {
    return (
      <p className="font-mono text-sm text-fg-mid">
        No price history for this itinerary yet.
      </p>
    );
  }
  const sources = [...new Set(points.map((p) => p.source))];
  const option: EChartsOption = {
    animation: false,
    grid: { left: 52, right: 16, top: 30, bottom: 40 },
    legend: {
      top: 0,
      textStyle: { color: t.fgMid, fontFamily: t.mono, fontSize: 11 },
      itemWidth: 14,
    },
    tooltip: {
      trigger: "axis",
      backgroundColor: t.bg2,
      borderColor: t.lineBright,
      textStyle: { color: t.fg, fontFamily: t.mono, fontSize: 12 },
      valueFormatter: (v) => `${v} ${currency}`,
    },
    xAxis: {
      type: "time",
      axisLabel: { color: t.fgMid, fontFamily: t.mono, fontSize: 10 },
      axisLine: { lineStyle: { color: t.line } },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: t.fgMid, fontFamily: t.mono, fontSize: 10 },
      splitLine: { lineStyle: { color: t.line } },
    },
    series: sources.map((s) => ({
      name: s,
      type: "line",
      showSymbol: true,
      symbolSize: 6,
      lineStyle: { width: 1.5 },
      color: SOURCE_COLORS[s] ?? t.fg,
      data: points
        .filter((p) => p.source === s)
        .map((p) => [p.snapshotAt, p.price]),
    })),
  };
  return <EChart option={option} height={300} />;
}
