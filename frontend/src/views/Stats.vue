<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { barChart, lineChart, stackedBarChart } from '../charts'
import BaseChart from '../components/BaseChart.vue'
import { fmtInt, fmtTime } from '../format'
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

const l1Cards = computed(() => {
  const l1 = data.stats?.level1
  return [
    { label: '一级池容量', value: fmtInt(l1?.pool_size) },
    { label: '累计拉取', value: fmtInt(l1?.total_pulled) },
    { label: '累计入池', value: fmtInt(l1?.total_entered) },
    { label: '累计去重', value: fmtInt(l1?.total_duplicates) },
    { label: '代理层调用', value: fmtInt(data.stats?.proxy?.total_calls) },
    { label: '累计错误', value: fmtInt(data.overview?.errors_total) },
  ]
})

const seriesNames = computed(() => Object.keys(history.value?.series || {}))

const pullRateOption = computed(() => {
  const series = history.value?.series || {}
  const labels = (series['global'] || []).map((p) => fmtTime(p.ts, range.value))
  const lines = seriesNames.value.map((n) => ({
    name: n,
    data: (series[n] || []).map((p) => p.pull_rate),
  }))
  return lineChart(labels, lines, { yName: 'IP/s' })
})

const passRateOption = computed(() => {
  const series = history.value?.series || {}
  const labels = (series['global'] || []).map((p) => fmtTime(p.ts, range.value))
  const lines = seriesNames.value.map((n) => ({
    name: n,
    data: (series[n] || []).map((p) => p.pass_rate),
  }))
  return lineChart(labels, lines, { yName: '通过率' })
})

const duplicateRateOption = computed(() => {
  const series = history.value?.series || {}
  const labels = (series['global'] || []).map((p) => fmtTime(p.ts, range.value))
  const lines = ['level1'].map((n) => ({
    name: '一级池重复率',
    data: (series[n] || []).map((p) => p.duplicate_rate),
  }))
  return lineChart(labels, lines, { yName: '重复率' })
})

const errorKeys = [
  'pull_failures',
  'test_failures',
  'sync_failures',
  'revalidate_failures',
  'ttl_sweep_failures',
  'empty_acquires',
  'drops',
]

const errorsOption = computed(() => {
  const g = history.value?.series['global'] || []
  const labels = g.map((p) => fmtTime(p.ts, range.value))
  const lines = errorKeys.map((k) => ({
    name: k,
    data: g.map((p) => p.errors[k] || 0),
  }))
  return stackedBarChart(labels, lines)
})

const ttlOption = computed(() =>
  barChart((data.distributions?.ttl || []).map((d) => ({ name: d.name, value: d.value }))),
)

const ipCountOption = computed(() => {
  const series = history.value?.series || {}
  const labels = (series['global'] || []).map((p) => fmtTime(p.ts, range.value))
  const lines = ['level1'].map((n) => ({
    name: '一级池 IP 数',
    data: (series[n] || []).map((p) => p.pool_capacity),
  }))
  return lineChart(labels, lines, { yName: '数量' })
})
</script>

<template>
  <div>
    <el-row :gutter="12">
      <el-col v-for="c in l1Cards" :key="c.label" :span="4" class="mb">
        <el-card shadow="hover">
          <div class="label">{{ c.label }}</div>
          <div class="value">{{ c.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card-header">
          <span>统计分析（历史折线）</span>
          <el-radio-group v-model="range" size="small">
            <el-radio-button v-for="(label, k) in RANGE_LABELS" :key="k" :value="k">
              {{ label }}
            </el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <el-alert v-if="historyError" :title="'历史数据加载失败：' + historyError" type="warning" :closable="false" class="mb" />
      <el-row :gutter="12">
        <el-col :span="12" class="mb">
          <el-card shadow="never">
            <template #header><span>历史 IP 数量</span></template>
            <BaseChart :option="ipCountOption" :dark="app.dark" height="260px" />
          </el-card>
        </el-col>
        <el-col :span="12" class="mb">
          <el-card shadow="never">
            <template #header><span>拉取速率</span></template>
            <BaseChart :option="pullRateOption" :dark="app.dark" height="260px" />
          </el-card>
        </el-col>
        <el-col :span="12" class="mb">
          <el-card shadow="never">
            <template #header><span>可达性测试通过率</span></template>
            <BaseChart :option="passRateOption" :dark="app.dark" height="260px" />
          </el-card>
        </el-col>
        <el-col :span="12" class="mb">
          <el-card shadow="never">
            <template #header><span>重复率</span></template>
            <BaseChart :option="duplicateRateOption" :dark="app.dark" height="260px" />
          </el-card>
        </el-col>
        <el-col :span="12" class="mb">
          <el-card shadow="never">
            <template #header><span>错误统计（各错误类型）</span></template>
            <BaseChart :option="errorsOption" :dark="app.dark" height="280px" />
          </el-card>
        </el-col>
        <el-col :span="12" class="mb">
          <el-card shadow="never">
            <template #header><span>池内 IP 剩余时间分布</span></template>
            <BaseChart :option="ttlOption" :dark="app.dark" height="280px" />
          </el-card>
        </el-col>
      </el-row>
    </el-card>

    <el-card shadow="never">
      <template #header><span>一级池错误明细</span></template>
      <el-descriptions :column="3" border>
        <el-descriptions-item
          v-for="(v, k) in data.stats?.level1?.errors || {}"
          :key="k"
          :label="k"
        >
          <span :class="{ red: v > 0 }">{{ v }}</span>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<style scoped>
.mb {
  margin-bottom: 12px;
}
.label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.value {
  font-size: 22px;
  font-weight: 600;
  margin-top: 6px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.red {
  color: #f56c6c;
}
</style>
