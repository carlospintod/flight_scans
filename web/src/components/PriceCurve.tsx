"use client";

import type { EChartsOption } from "echarts";
import { EChart, chartTheme as t } from "./EChart";

/** Cheapest one-way price per departure day — the one-way analogue of
 *  the round-trip heatmap. A line over the departure window. */
export function PriceCurve({
  points,
  currency,
}: {
  points: { departureDate: string; price: number }[];
  currency: string;
}) {
  if (points.length === 0) {
    return (
      <p className="font-mono text-sm text-fg-mid">
        No one-way prices inside the window yet — the next scan fills this in.
      </p>
    );
  }
  const option: EChartsOption = {
    animation: false,
    grid: { left: 52, right: 16, top: 16, bottom: 40 },
    tooltip: {
      trigger: "axis",
      backgroundColor: t.bg2,
      borderColor: t.lineBright,
      textStyle: { color: t.fg, fontFamily: t.mono, fontSize: 12 },
      valueFormatter: (v) => `${v} ${currency}`,
    },
    xAxis: {
      type: "category",
      data: points.map((p) => p.departureDate),
      axisLabel: {
        color: t.fgMid, fontFamily: t.mono, fontSize: 10, rotate: 45,
        formatter: (d: string) => d.slice(5),
      },
      axisLine: { lineStyle: { color: t.line } },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: `price (${currency})`,
      nameTextStyle: { color: t.fgDim, fontFamily: t.mono, fontSize: 10 },
      axisLabel: { color: t.fgMid, fontFamily: t.mono, fontSize: 10 },
      splitLine: { lineStyle: { color: t.line } },
    },
    series: [
      {
        type: "line",
        smooth: true,
        showSymbol: true,
        symbolSize: 5,
        lineStyle: { width: 2, color: t.matrix },
        itemStyle: { color: t.matrix },
        areaStyle: { color: "rgba(0,255,65,0.06)" },
        data: points.map((p) => p.price),
      },
    ],
  };
  const minWidth = Math.max(560, points.length * 20 + 80);
  return (
    <div className="overflow-x-auto">
      <div style={{ minWidth }}>
        <EChart option={option} height={280} />
      </div>
    </div>
  );
}
