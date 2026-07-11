"use client";

import { useEffect, useRef } from "react";
import * as echarts from "echarts";

/** Minimal ECharts wrapper (own ~30 lines instead of echarts-for-react:
 *  fewer peer-dep surprises on React 19 / Next 16). Re-renders on option
 *  change, resizes with the container. */
export function EChart({
  option,
  height = 320,
  className = "",
}: {
  option: echarts.EChartsOption;
  height?: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true });
  }, [option]);

  return <div ref={ref} style={{ height }} className={className} />;
}

/** Shared dark-theme fragments — the ONE place chart hues live, synced
 *  to the Phosphor v4 tokens in globals.css (canvas needs literal values;
 *  keep these in step with the @theme block when tokens change). */
export const chartTheme = {
  text: "#cdd6f4",
  textMid: "#a6adc8",
  hint: "#7f849c",
  border: "#313244",
  borderBright: "#45475a",
  bg: "#181825",
  bg2: "#1e1e2e",
  good: "#a6e3a1",       /* live / cheap — the price color */
  softGreen: "#c2ecbe",  /* good, emphasized */
  cyan: "#89b4fa",
  amber: "#fab387",
  red: "#ff6b81",
  kiwi: "#b57bff",       /* tool-specific series hues (this repo only) */
  aviasales: "#ff8c42",
  mono: "IBM Plex Mono, ui-monospace, monospace",
} as const;
