<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { barChart, lineChart } from '../charts'
import BaseChart from '../components/BaseChart.vue'
import { fmtInt, fmtMs, fmtPct, fmtRate, fmtTime } from '../format'
import { useAppStore } from '../stores/app'
import { useDataStore } from '../stores/data'
import type { HistoryResponse } from '../types'

const data = useDataStore()
const app = useAppStore()

const range = ref('24h')
const history = ref<HistoryResponse | null>(null)
const historyError = ref<string | null>(null)

const RANGE_LABELS: Record<string, string> = {
  '1h': '近1小时',
  '6h': '近6小时',
  '24h': '近24小时',
  '7d': '近7天',
}

async function loadHistory() {
  try {
    history.value = await api.history(range.value)
    historyError.value = null
  } catch (e) {
    historyError.value = e instanceof Error ? e.message : '历史加载失败'
  }
}

onMounted(loadHistory)
watch(range, loadHistory)

const cards = computed(() => {
  const ov = data.overview
  return [
    { label: '池容量', value: fmtInt(ov?.pool_capacity) },
    { label: '可用 IP', value: fmtInt(ov?.available_count) },
    { label: '租赁中', value: fmtInt(ov?.leased_count) },
    { label: '平均延迟', value: fmtMs(ov?.avg_latency) },
    { label: '拉取速率', value: fmtRate(ov?.pull_rate) },
    { label: '测试通过率', value: fmtPct(ov?.pass_rate) },
    { label: '重复率', value: fmtPct(ov?.duplicate_rate) },
    { label: '错误总数', value: fmtInt(ov?.errors_total) },
  ]
})

const latencyOption = computed(() => {
  const colors = ['#67C23A', '#67C23A', '#E6A23C', '#E6A23C', '#F56C6C', '#F56C6C']
  const items = (data.distributions?.latency || []).map((d, i) => ({
    name: d.name,
    value: d.value,
    color: colors[i % colors.length],
  }))
  return barChart(items)
})

const protoOption = computed(() => {
  const p = data.overview?.by_proto
  const names = ['http', 'https', 'socks4', 'socks5']
  const colors = ['#409EFF', '#67C23A', '#E6A23C', '#9C27B0']
  return barChart(
    names.map((n, i) => ({ name: n, value: p ? p[n as keyof typeof p] : 0, color: colors[i] })),
  )
})

const globalPoints = computed(() => history.value?.series['global'] || [])

const ipCountOption = computed(() =>
  lineChart(
    globalPoints.value.map((p) => fmtTime(p.ts, range.value)),
    [
      { name: '池容量', data: globalPoints.value.map((p) => p.pool_capacity), color: '#409EFF' },
      { name: '可用 IP', data: globalPoints.value.map((p) => p.available_count), color: '#67C23A' },
    ],
    { yName: '数量' },
  ),
)

const latencyHistoryOption = computed(() =>
  lineChart(
    globalPoints.value.map((p) => fmtTime(p.ts, range.value)),
    [{ name: '平均延迟', data: globalPoints.value.map((p) => p.avg_latency), color: '#E6A23C' }],
    { yName: 'ms' },
  ),
)
</script>

<template>
  <div>
    <el-row :gutter="12">
      <el-col v-for="c in cards" :key="c.label" :span="6" class="mb">
        <el-card shadow="hover">
          <div class="card-label">{{ c.label }}</div>
          <div class="card-value">{{ c.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card-header">
          <span>历史趋势</span>
          <el-radio-group v-model="range" size="small">
            <el-radio-button v-for="(label, k) in RANGE_LABELS" :key="k" :value="k">
              {{ label }}
            </el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <el-alert v-if="historyError" :title="'历史数据加载失败：' + historyError" type="warning" :closable="false" class="mb" />
      <el-row :gutter="12">
        <el-col :span="12">
          <BaseChart :option="ipCountOption" :dark="app.dark" height="300px" />
        </el-col>
        <el-col :span="12">
          <BaseChart :option="latencyHistoryOption" :dark="app.dark" height="300px" />
        </el-col>
      </el-row>
    </el-card>

    <el-row :gutter="12">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header><span>延迟分布</span></template>
          <BaseChart :option="latencyOption" :dark="app.dark" height="280px" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header><span>协议分布（可用 IP）</span></template>
          <BaseChart :option="protoOption" :dark="app.dark" height="280px" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.mb {
  margin-bottom: 12px;
}
.card-label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.card-value {
  font-size: 26px;
  font-weight: 600;
  margin-top: 6px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
