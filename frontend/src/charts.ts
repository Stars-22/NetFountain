import type { EChartsOption } from 'echarts'

export interface BarItem {
  name: string
  value: number
  color?: string
}

export function barChart(items: BarItem[]): EChartsOption {
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 48, right: 16, top: 30, bottom: 30 },
    xAxis: { type: 'category', data: items.map((i) => i.name) },
    yAxis: { type: 'value' },
    series: [
      {
        type: 'bar',
        data: items.map((i) => ({
          value: i.value,
          itemStyle: i.color ? { color: i.color } : undefined,
        })),
        barMaxWidth: 48,
      },
    ],
  }
}

export interface LineSeries {
  name: string
  data: (number | null)[]
  color?: string
}

export function lineChart(
  labels: (string | number)[],
  series: LineSeries[],
  opts?: { yName?: string },
): EChartsOption {
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: series.map((s) => s.name), top: 0 },
    grid: { left: 56, right: 20, top: 40, bottom: 46 },
    xAxis: { type: 'category', data: labels, boundaryGap: false },
    yAxis: { type: 'value', name: opts?.yName },
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', start: 0, end: 100, height: 16, bottom: 4 },
    ],
    series: series.map((s) => ({
      name: s.name,
      type: 'line',
      data: s.data,
      showSymbol: false,
      smooth: true,
      connectNulls: true,
      lineStyle: s.color ? { color: s.color, width: 2 } : undefined,
      itemStyle: s.color ? { color: s.color } : undefined,
    })),
  }
}

export function stackedBarChart(
  labels: string[],
  series: LineSeries[],
): EChartsOption {
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: series.map((s) => s.name), top: 0 },
    grid: { left: 48, right: 16, top: 40, bottom: 46 },
    xAxis: { type: 'category', data: labels },
    yAxis: { type: 'value' },
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', start: 0, end: 100, height: 16, bottom: 4 },
    ],
    series: series.map((s) => ({
      name: s.name,
      type: 'bar',
      stack: 'total',
      data: s.data,
      barMaxWidth: 24,
    })),
  }
}
