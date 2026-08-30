import { BarChart, HeatmapChart as HeatmapSeries, RadarChart as RadarSeries } from "echarts/charts";
import { GridComponent, LegendComponent, RadarComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import * as echarts from "echarts/core";
import type { EChartsOption } from "echarts";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import type { RunSnapshot } from "../types";

echarts.use([BarChart, HeatmapSeries, RadarSeries, GridComponent, LegendComponent, RadarComponent, TooltipComponent, VisualMapComponent, SVGRenderer]);

const palette = ["#5EEAD4", "#7CB8FF", "#FF9B71", "#F6C453", "#A7D8FF", "#8BE0D0"];
const fallbackDimensions = ["基础查询", "连接与粒度", "聚合与指标", "时间与窗口", "复杂查询", "数据开发"];

function useChart(option: EChartsOption) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "svg" });
    chart.setOption(option, true);
    const resize = () => chart.resize();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    observer?.observe(ref.current);
    const frame = requestAnimationFrame(resize);
    window.addEventListener("resize", resize);
    return () => { cancelAnimationFrame(frame); observer?.disconnect(); window.removeEventListener("resize", resize); chart.dispose(); };
  }, [option]);
  return ref;
}

const base = { backgroundColor: "transparent", textStyle: { color: "#F5F7FA", fontFamily: "Noto Sans SC" }, tooltip: { trigger: "item", backgroundColor: "#111D29", borderColor: "#ffffff22", textStyle: { color: "#F5F7FA" } } } as const;

export function RankingChart({ report }: { report: RunSnapshot }) {
  const models = [...report.models].sort((a, b) => (b.official_score ?? 0) - (a.official_score ?? 0));
  const ref = useChart({ ...base, grid: { left: 112, right: 28, top: 16, bottom: 26 }, xAxis: { type: "value", min: 0, max: 100, axisLabel: { color: "#91A4B6" }, splitLine: { lineStyle: { color: "#ffffff10" } } }, yAxis: { type: "category", inverse: true, data: models.map((m) => m.name), axisLabel: { color: "#F5F7FA", width: 96, overflow: "truncate" }, axisLine: { show: false }, axisTick: { show: false } }, series: [{ type: "bar", data: models.map((model, index) => ({ value: Number((model.official_score ?? 0).toFixed(2)), itemStyle: { color: palette[index] } })), barWidth: 22, label: { show: true, position: "right", color: "#F5F7FA", formatter: "{c}" } }] });
  return <div ref={ref} className="chart"/>;
}

export function RadarChart({ report }: { report: RunSnapshot }) {
  const dimensions = Array.from(new Set(report.models.flatMap((model) => Object.keys(model.categories ?? {}))));
  const activeDimensions = dimensions.length ? dimensions : fallbackDimensions;
  const ref = useChart({ ...base, color: palette, legend: { bottom: 0, textStyle: { color: "#B8C6D3" } }, radar: { radius: "64%", center: ["50%", "47%"], indicator: activeDimensions.map((name) => ({ name, max: 100 })), axisName: { color: "#DCE6EF" }, splitLine: { lineStyle: { color: "#ffffff1a" } }, splitArea: { areaStyle: { color: ["#ffffff02", "#ffffff06"] } }, axisLine: { lineStyle: { color: "#ffffff1a" } } }, series: [{ type: "radar", data: report.models.map((model) => ({ name: model.name, value: activeDimensions.map((dimension) => Number(model.categories?.[dimension] ?? 0)), areaStyle: { opacity: .08 } })) }] });
  return <div ref={ref} className="chart"/>;
}

export function HeatmapChart({ report }: { report: RunSnapshot }) {
  const cases = report.models[0]?.cases.filter((item, index, all) => all.findIndex((other) => other.stable_key === item.stable_key) === index).map((item) => item.stable_key) ?? [];
  const data = report.models.flatMap((model, y) => cases.map((key, x) => [x, y, Number(([...model.cases].reverse().find((item) => item.stable_key === key)?.score?.total ?? 0).toFixed(2))]));
  const ref = useChart({ ...base, grid: { left: 140, right: 36, top: 24, bottom: 96 }, xAxis: { type: "category", data: cases.map((_, index) => String(index + 1).padStart(2, "0")), axisLabel: { color: "#91A4B6", interval: 0 } }, yAxis: { type: "category", data: report.models.map((m) => m.name), axisLabel: { color: "#F5F7FA", width: 120, overflow: "truncate" } }, visualMap: { min: 0, max: 100, orient: "horizontal", left: "center", bottom: 4, inRange: { color: ["#172333", "#FF9B71", "#5EEAD4"] }, textStyle: { color: "#91A4B6" } }, series: [{ type: "heatmap", data, label: { show: true, color: "#08111B", formatter: ({ value }) => Array.isArray(value) ? String(value[2]) : "" }, itemStyle: { borderColor: "#08111B", borderWidth: 3 } }] });
  return <div ref={ref} className="chart wide"/>;
}
