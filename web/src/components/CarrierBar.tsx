"use client";

import type { EChartsOption } from "echarts";
import { EChart, chartTheme as t } from "./EChart";
import type { CarrierCount } from "@/lib/types";

/** Who serves the cheap fares — rank-0 carrier strings, verbatim
 *  ('KLM + Kenya Airways' is its own bucket). Mirrors carrier_mix. */
export function CarrierBar({ carriers }: { carriers: CarrierCount[] }) {
  if (carriers.length === 0) {
    return (
      <p className="font-mono text-sm text-fg-mid">
        No verified carrier detail inside the window yet.
      </p>
    );
  }
  const rows = [...carriers].reverse(); // largest on top
  const option: EChartsOption = {
    animation: false,
    grid: { left: 8, right: 40, top: 8, bottom: 24, containLabel: true },
    tooltip: {
      backgroundColor: t.bg2,
      borderColor: t.lineBright,
      textStyle: { color: t.fg, fontFamily: t.mono, fontSize: 12 },
    },
    xAxis: {
      type: "value",
      axisLabel: { color: t.fgDim, fontFamily: t.mono, fontSize: 10 },
      splitLine: { lineStyle: { color: t.line } },
    },
    yAxis: {
      type: "category",
      data: rows.map((c) => c.carrier),
      axisLabel: {
        color: t.fg,
        fontFamily: t.mono,
        fontSize: 11,
        width: 170,
        overflow: "truncate",
      },
      axisLine: { lineStyle: { color: t.line } },
      axisTick: { show: false },
    },
    series: [
      {
        type: "bar",
        data: rows.map((c) => c.n),
        barMaxWidth: 14,
        itemStyle: { color: t.matrixDim },
        emphasis: { itemStyle: { color: t.matrix } },
        label: {
          show: true,
          position: "right",
          color: t.fgMid,
          fontFamily: t.mono,
          fontSize: 10,
        },
      },
    ],
  };
  return <EChart option={option} height={Math.max(180, rows.length * 28 + 60)} />;
}
