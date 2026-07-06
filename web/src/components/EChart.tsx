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

/** Shared dark-theme fragments matching the GTM99 tokens. */
export const chartTheme = {
  fg: "#c8cad8",
  fgMid: "#8e91a8",
  fgDim: "#585b72",
  line: "#1e1f32",
  lineBright: "#2c2d44",
  bg2: "#0f1018",
  matrix: "#00ff41",
  matrixDim: "#00b830",
  cyan: "#00d4ff",
  amber: "#ffcc00",
  danger: "#ff4455",
  mono: "IBM Plex Mono, ui-monospace, monospace",
} as const;
