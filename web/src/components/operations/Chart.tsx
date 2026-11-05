import { trafficScale } from "@/lib/utils";
import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart, BarChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  AriaComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsCoreOption } from "echarts/core";
echarts.use([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  AriaComponent,
  CanvasRenderer,
]);
interface Series {
  name: string;
  values: (number | null)[];
  color?: string;
  area?: boolean;
}
export function Chart({
  labels,
  series,
  kind = "line",
  unit = "",
  label,
}: {
  labels: string[];
  series: Series[];
  kind?: "line" | "bar";
  unit?: string;
  label: string;
}) {
  const element = useRef<HTMLDivElement>(null),
    instance = useRef<echarts.ECharts | null>(null);
  const props = useRef({ labels, series, kind, unit, label });
  props.current = { labels, series, kind, unit, label };
  function render() {
    const data = props.current;
    const color = getComputedStyle(document.documentElement)
      .getPropertyValue("--accent")
      .trim();
    const traffic =
      data.unit === " B/s" ||
      data.unit === " B" ||
      data.unit === "B/s" ||
      data.unit === "B";
    const scale = traffic
      ? trafficScale(
          data.series.flatMap((s) => s.values),
          data.unit.trim() === "B/s",
        )
      : { divisor: 1, unit: data.unit.trim() };
    const number = (v: number) =>
      Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const option: EChartsCoreOption = {
      animation: !reduce,
      animationDuration: 250,
      backgroundColor: "transparent",
      aria: {
        enabled: true,
        label: {
          description: `${data.label}。共 ${data.labels.length} 个时间点；缺测保留为空档。`,
        },
      },
      textStyle: { color: "#8e9bb0", fontFamily: "inherit", fontSize: 12 },
      grid: { top: 24, bottom: 44, left: 58, right: 16 },
      tooltip: {
        trigger: "axis",
        confine: true,
        backgroundColor: "#151a24",
        borderColor: "#394354",
        textStyle: { color: "#d9e2f2", fontSize: 13 },
        valueFormatter: (v: unknown) =>
          `${number(Number(v))}${scale.unit ? " " + scale.unit : ""}`,
      },
      legend: {
        bottom: 0,
        left: 12,
        itemWidth: 12,
        itemHeight: 3,
        textStyle: { color: "#8e9bb0", fontSize: 12 },
      },
      xAxis: {
        type: "category",
        data: data.labels,
        boundaryGap: data.kind === "bar",
        axisLine: { lineStyle: { color: "#28303c" } },
        axisTick: { show: false },
        axisLabel: { color: "#78859a", fontSize: 12, hideOverlap: true },
      },
      yAxis: {
        type: "value",
        name: scale.unit,
        nameTextStyle: { fontSize: 12, color: "#78859a" },
        splitNumber: 3,
        axisLabel: {
          color: "#78859a",
          fontSize: 12,
          formatter: (v: number) =>
            traffic
              ? number(v)
              : v >= 1e6
                ? `${number(v / 1e6)}M`
                : v >= 1e3
                  ? `${number(v / 1e3)}k`
                  : number(v),
        },
        splitLine: { lineStyle: { color: "#ffffff08" } },
      },
      series: data.series.map((s, i) => ({
        name: s.name,
        type: data.kind,
        data: s.values.map((v) => (v === null ? null : v / scale.divisor)),
        connectNulls: false,
        showSymbol: s.values.length < 3,
        symbolSize: 4,
        smooth: false,
        lineStyle: { width: 1.7 },
        itemStyle: { color: s.color ?? [color, "#e0ad73", "#b399e9"][i % 3] },
        areaStyle: s.area ? { opacity: 0.08 } : undefined,
        barMaxWidth: 38,
      })),
    };
    instance.current?.setOption(option, { notMerge: true });
  }
  useEffect(() => {
    if (!element.current) return;
    instance.current = echarts.init(element.current);
    render();
    const resize = new ResizeObserver(() => instance.current?.resize());
    resize.observe(element.current);
    const theme = new MutationObserver(render);
    theme.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => {
      theme.disconnect();
      resize.disconnect();
      instance.current?.dispose();
      instance.current = null;
    };
  }, []);
  useEffect(render, [labels, series, kind, unit]);
  return (
    <div className="ops-chart" role="img" aria-label={label} ref={element} />
  );
}
