<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { lineChart } from '../charts'
import BaseCardCell from '../components/BaseCardCell.vue'
import BaseChart from '../components/BaseChart.vue'
import RefreshSelector from '../components/RefreshSelector.vue'
import { ERROR_LABELS, PALETTE, fmtDuration, fmtInt, fmtTime } from '../format'
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
const removeHistoryHook = data.addHook(loadHistory)
onBeforeUnmount(() => removeHistoryHook())

// ---- 代理层 ----
const proxyCards = computed(() => [
  { label: '代理层启动时长', value: fmtDuration(data.stats?.proxy?.uptime) },
  { label: '代理层 API 被调用次数', value: fmtInt(data.stats?.proxy?.total_calls) },
])

const callsByIp = computed(() =>
  Object.entries(data.stats?.proxy?.calls_by_ip ?? {})
    .map(([ip, count]) => ({ ip, count }))
    .sort((a, b) => b.count - a.count),
)

const callsBySite = computed(() =>
  Object.entries(data.stats?.proxy?.calls_by_site ?? {}).map(([site, count]) => ({
    site,
    count,
  })),
)

// ---- 历史多色折线 ----
// 一级池 + 所有二级池（排除 global 聚合序列）
const poolKeys = computed(() =>
  Object.keys(history.value?.series ?? {}).filter((k) => k !== 'global'),
)
// 仅二级池
const siteKeys = computed(() => poolKeys.value.filter((k) => k !== 'level1'))

function pointsOf(key: string) {
  return history.value?.series[key] ?? []
}

function labelsOf() {
  const ref0 = history.value?.series['global'] ?? history.value?.series['level1'] ?? []
  return ref0.map((p) => fmtTime(p.ts, range.value))
}

function multiLine(field: 'pool_capacity' | 'pass_rate' | 'pull_rate' | 'avg_latency', keys: string[]) {
  return lineChart(
    labelsOf(),
    keys.map((k, i) => ({
      name: k,
      data: pointsOf(k).map((p) => p[field]),
      color: PALETTE[i % PALETTE.length],
    })),
    field === 'avg_latency' ? { yName: 'ms' } : field === 'pull_rate' ? { yName: 'IP/s' } : undefined,
  )
}

const ipCountOption = computed(() => multiLine('pool_capacity', poolKeys.value))
const passRateOption = computed(() => multiLine('pass_rate', poolKeys.value))
const pullRateOption = computed(() => multiLine('pull_rate', poolKeys.value))
const latencyOption = computed(() => multiLine('avg_latency', siteKeys.value))

const ERROR_KEYS = [
  'pull_failures',
  'test_failures',
  'sync_failures',
  'revalidate_failures',
  'ttl_sweep_failures',
  'empty_acquires',
  'drops',
]

// 历史错误统计：每种错误一条线（一二级池合并 = global 序列）
const errorsOption = computed(() =>
  lineChart(
    (history.value?.series['global'] ?? []).map((p) => fmtTime(p.ts, range.value)),
    ERROR_KEYS.map((k, i) => ({
      name: ERROR_LABELS[k] || k,
      data: (history.value?.series['global'] ?? []).map((p) => p.errors[k] ?? 0),
      color: PALETTE[i % PALETTE.length],
    })),
  ),
)
</script>

<template>
  <div>
    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card-header">
          <span>代理层</span>
          <RefreshSelector />
        </div>
      </template>
      <div class="strip">
        <BaseCardCell v-for="c in proxyCards" :key="c.label" :label="c.label" :value="c.value" />
      </div>
    </el-card>

    <el-card shadow="never" class="mb">
      <template #header><span>按来源 IP 的调用次数</span></template>
      <el-table :data="callsByIp" stripe max-height="260">
        <el-table-column prop="ip" label="来源 IP" min-width="200" />
        <el-table-column prop="count" label="调用次数" width="140" sortable />
      </el-table>
    </el-card>

    <el-card shadow="never" class="mb">
      <template #header><span>向各二级池的转发次数</span></template>
      <div v-if="callsBySite.length" class="health-row">
        <div v-for="c in callsBySite" :key="c.site" class="health-item">
          <span class="health-label">{{ c.site }}</span>
          <span class="health-value">{{ c.count }}</span>
        </div>
      </div>
      <el-empty v-else description="暂无转发记录" :image-size="60" />
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>统计图表</span>
          <el-radio-group v-model="range" size="small">
            <el-radio-button v-for="(label, k) in RANGE_LABELS" :key="k" :value="k">
              {{ label }}
            </el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <el-alert
        v-if="historyError"
        :title="'历史数据加载失败：' + historyError"
        type="warning"
        :closable="false"
        class="mb"
      />
      <el-row :gutter="12">
        <el-col :span="12" class="mb">
          <el-card shadow="never">
            <template #header><span>历史池内 IP 数量（一级池 + 全部二级池）</span></template>
            <BaseChart :option="ipCountOption" :dark="app.dark" height="260px" />
          </el-card>
        </el-col>
        <el-col :span="12" class="mb">
          <el-card shadow="never">
            <template #header><span>历史测试通过率（一级池 + 全部二级池）</span></template>
            <BaseChart :option="passRateOption" :dark="app.dark" height="260px" />
          </el-card>
        </el-col>
        <el-col :span="12" class="mb">
          <el-card shadow="never">
            <template #header><span>拉取速率（一级池 + 全部二级池）</span></template>
            <BaseChart :option="pullRateOption" :dark="app.dark" height="260px" />
          </el-card>
        </el-col>
        <el-col :span="12" class="mb">
          <el-card shadow="never">
            <template #header><span>历史平均延迟（全部二级池）</span></template>
            <BaseChart :option="latencyOption" :dark="app.dark" height="260px" />
          </el-card>
        </el-col>
        <el-col :span="24" class="mb">
          <el-card shadow="never">
            <template #header><span>历史错误统计（一级池 + 二级池合并，每种错误一条线）</span></template>
            <BaseChart :option="errorsOption" :dark="app.dark" height="280px" />
          </el-card>
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<style scoped>
.mb {
  margin-bottom: 12px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 0;
}
.health-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 0;
}
.health-item {
  display: flex;
  flex-direction: column;
  min-width: 110px;
  padding: 0 16px;
  border-right: 1px solid var(--el-border-color-lighter);
}
.health-item:last-child {
  border-right: none;
}
.health-label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
}
.health-value {
  font-size: 18px;
  font-weight: 600;
  margin-top: 4px;
}
</style>
