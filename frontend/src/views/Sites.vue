<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { barChart, lineChart, pieChart } from '../charts'
import BaseCardCell from '../components/BaseCardCell.vue'
import BaseChart from '../components/BaseChart.vue'
import RefreshSelector from '../components/RefreshSelector.vue'
import { ERROR_LABELS, PALETTE, fmtDuration, fmtInt, fmtMs, fmtPct, fmtTime } from '../format'
import { useAppStore } from '../stores/app'
import { useDataStore } from '../stores/data'
import type { HistoryResponse, Level1Strip, SiteStrip } from '../types'

const LEVEL1_TAB = '__level1__'

const data = useDataStore()
const app = useAppStore()

const activeTab = ref(LEVEL1_TAB)
const range = ref('24h')
const history = ref<HistoryResponse | null>(null)
const historyError = ref<string | null>(null)
const releasing = ref(false)

const RANGE_LABELS: Record<string, string> = {
  '1h': '近1小时',
  '6h': '近6小时',
  '24h': '近24小时',
  '7d': '近7天',
}

const siteStrips = computed<SiteStrip[]>(() => data.overview?.sites ?? [])
const l1 = computed<Level1Strip | null>(() => data.overview?.level1 ?? null)

const isLevel1 = computed(() => activeTab.value === LEVEL1_TAB)
const currentSite = computed<SiteStrip | null>(
  () => siteStrips.value.find((s) => s.name === activeTab.value) ?? null,
)
// 历史序列键：一级池为 'level1'，二级池为站点名
const seriesKey = computed(() => (isLevel1.value ? 'level1' : activeTab.value))

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

function pointsOf(key: string) {
  return history.value?.series[key] ?? []
}

// ---- 基础信息 ----
const l1InfoCells = computed(() => {
  const s = l1.value
  return [
    { label: 'IP 数', value: fmtInt(s?.ip_count) },
    { label: '启动时长', value: fmtDuration(s?.uptime) },
    { label: '总拉取数量', value: fmtInt(s?.total_pulled) },
    { label: '测试通过率', value: fmtPct(s?.pass_rate) },
    { label: '重复率', value: fmtPct(s?.duplicate_rate) },
    { label: '错误总数', value: fmtInt(s?.errors_total) },
    { label: 'API 调用次数', value: fmtInt(s?.api_call_count) },
    { label: '平均剩余时间', value: fmtDuration(s?.avg_remaining) },
  ]
})

const siteInfoCells = computed(() => {
  const s = currentSite.value
  if (!s) return []
  return [
    { label: 'IP 数', value: fmtInt(s.ip_count) },
    { label: '可用 IP', value: fmtInt(s.free) },
    { label: '租赁中', value: fmtInt(s.leased) },
    { label: '启动时长', value: fmtDuration(s.uptime) },
    { label: '总拉取数量', value: fmtInt(s.total_pulled) },
    { label: '测试通过率', value: fmtPct(s.pass_rate) },
    { label: '平均延迟', value: fmtMs(s.avg_latency) },
    { label: '错误总数', value: fmtInt(s.errors_total) },
    { label: 'API 调用次数', value: fmtInt(s.api_call_count) },
    { label: '平均剩余时间', value: fmtDuration(s.avg_remaining) },
    { label: '目标站点', value: s.target_url || '-' },
  ]
})

// ---- 健康指标 ----
function healthItems(errors: Record<string, number>, drops: number, keys: string[]) {
  return keys.map((k) => ({
    label: ERROR_LABELS[k] || k,
    value: k === 'drops' ? drops : (errors[k] ?? 0),
  }))
}

const l1Health = computed(() =>
  healthItems(l1.value?.errors ?? {}, l1.value?.drops ?? 0, [
    'pull_failures',
    'test_failures',
    'ttl_sweep_failures',
    'drops',
  ]),
)

const siteHealth = computed(() =>
  healthItems(currentSite.value?.errors ?? {}, currentSite.value?.drops ?? 0, [
    'sync_failures',
    'test_failures',
    'revalidate_failures',
    'ttl_sweep_failures',
    'empty_acquires',
    'drops',
  ]),
)

// ---- 图表 ----
const poolCountOption = computed(() =>
  lineChart(
    pointsOf(seriesKey.value).map((p) => fmtTime(p.ts, range.value)),
    [
      {
        name: '池内 IP 数量',
        data: pointsOf(seriesKey.value).map((p) => p.pool_capacity),
        color: PALETTE[0],
      },
    ],
    { yName: '数量' },
  ),
)

const freeCountOption = computed(() =>
  lineChart(
    pointsOf(seriesKey.value).map((p) => fmtTime(p.ts, range.value)),
    [
      {
        name: '可用 IP 数量',
        data: pointsOf(seriesKey.value).map((p) => p.available_count),
        color: PALETTE[1],
      },
    ],
    { yName: '数量' },
  ),
)

const leasedCountOption = computed(() =>
  lineChart(
    pointsOf(seriesKey.value).map((p) => fmtTime(p.ts, range.value)),
    [
      {
        name: '已租 IP 数量',
        data: pointsOf(seriesKey.value).map((p) => p.leased_count),
        color: PALETTE[3],
      },
    ],
    { yName: '数量' },
  ),
)

const passRateOption = computed(() =>
  lineChart(
    pointsOf(seriesKey.value).map((p) => fmtTime(p.ts, range.value)),
    [
      {
        name: '测试通过率',
        data: pointsOf(seriesKey.value).map((p) => p.pass_rate),
        color: PALETTE[1],
      },
    ],
    { yName: '通过率' },
  ),
)

const dupRateOption = computed(() =>
  lineChart(
    pointsOf('level1').map((p) => fmtTime(p.ts, range.value)),
    [
      {
        name: '重复率',
        data: pointsOf('level1').map((p) => p.duplicate_rate),
        color: PALETTE[3],
      },
    ],
    { yName: '重复率' },
  ),
)

const latencyHistOption = computed(() =>
  lineChart(
    pointsOf(seriesKey.value).map((p) => fmtTime(p.ts, range.value)),
    [
      {
        name: '平均延迟',
        data: pointsOf(seriesKey.value).map((p) => p.avg_latency),
        color: PALETTE[2],
      },
    ],
    { yName: 'ms' },
  ),
)

const ttlOption = computed(() =>
  barChart(data.distributions?.pools[seriesKey.value]?.ttl ?? []),
)

const latencyDistOption = computed(() =>
  barChart(data.distributions?.pools[seriesKey.value]?.latency ?? []),
)

const protoOption = computed(() => {
  const p = isLevel1.value ? l1.value?.by_proto : currentSite.value?.by_proto
  const colors: Record<string, string> = {
    http: PALETTE[0],
    https: PALETTE[1],
    socks4: PALETTE[2],
    socks5: PALETTE[3],
  }
  if (!p) return pieChart([])
  return pieChart(
    (['http', 'https', 'socks4', 'socks5'] as const)
      .map((k) => ({ name: k, value: p[k], color: colors[k] }))
      .filter((i) => i.value > 0),
  )
})

const level1Charts = computed(() => [
  { title: '历史池内 IP 数量', option: poolCountOption.value },
  { title: '池内 IP 剩余时间分布', option: ttlOption.value },
  { title: '协议分布', option: protoOption.value },
  { title: '历史测试通过率', option: passRateOption.value },
  { title: '历史重复率', option: dupRateOption.value },
])

const siteCharts = computed(() => [
  { title: '历史池内 IP 数量', option: poolCountOption.value },
  { title: '历史可用 IP 数', option: freeCountOption.value },
  { title: '历史已租 IP 数', option: leasedCountOption.value },
  { title: '池内 IP 剩余时间分布', option: ttlOption.value },
  { title: '延迟分布', option: latencyDistOption.value },
  { title: '历史平均延迟', option: latencyHistOption.value },
  { title: '协议分布', option: protoOption.value },
  { title: '历史测试通过率', option: passRateOption.value },
])

// ---- 一键释放 ----
async function confirmRelease() {
  const name = activeTab.value
  if (isLevel1.value || !name) return
  try {
    await ElMessageBox.confirm(
      `确认释放二级池「${name}」的全部租赁 IP？该操作调用上游 release-all，不可撤销。`,
      '一键释放',
      { type: 'warning', confirmButtonText: '释放', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  releasing.value = true
  try {
    const count = await api.releaseAll(name)
    ElMessage.success(`已释放 ${count} 个租赁 IP`)
    data.refreshOnce()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '释放失败')
  } finally {
    releasing.value = false
  }
}
</script>

<template>
  <div>
    <div class="toolbar">
      <el-tabs v-model="activeTab" type="card" class="tabs">
        <el-tab-pane :name="LEVEL1_TAB">
          <template #label><span>一级 IP 池</span></template>
        </el-tab-pane>
        <el-tab-pane v-for="s in siteStrips" :key="s.name" :name="s.name">
          <template #label>
            <span>
              {{ s.name }}
              <el-tag
                :type="!s.reachable ? 'danger' : s.stale ? 'warning' : 'success'"
                size="small"
              >
                {{ !s.reachable ? '离线' : s.stale ? '数据延迟' : '在线' }}
              </el-tag>
            </span>
          </template>
        </el-tab-pane>
      </el-tabs>
      <div class="toolbar-right">
        <el-radio-group v-model="range" size="small">
          <el-radio-button v-for="(label, k) in RANGE_LABELS" :key="k" :value="k">
            {{ label }}
          </el-radio-button>
        </el-radio-group>
        <el-button
          v-if="!isLevel1"
          type="danger"
          plain
          size="small"
          :loading="releasing"
          @click="confirmRelease"
        >
          一键释放全部 IP
        </el-button>
        <RefreshSelector />
      </div>
    </div>

    <el-alert
      v-if="historyError"
      :title="'历史数据加载失败：' + historyError"
      type="warning"
      :closable="false"
      class="mb"
    />

    <!-- 一级 IP 池 -->
    <template v-if="isLevel1 && l1">
      <el-card shadow="never" class="mb">
        <div class="strip">
          <BaseCardCell v-for="c in l1InfoCells" :key="c.label" :label="c.label" :value="c.value" />
        </div>
      </el-card>

      <el-card shadow="never" class="mb">
        <div class="health-row">
          <div
            v-for="h in l1Health"
            :key="h.label"
            class="health-item"
            :class="{ red: h.value > 0 }"
          >
            <span class="health-label">{{ h.label }}</span>
            <span class="health-value">{{ h.value }}</span>
          </div>
        </div>
      </el-card>

      <el-row :gutter="12">
        <el-col v-for="c in level1Charts" :key="c.title" :span="8" class="mb">
          <el-card shadow="never">
            <template #header><span>{{ c.title }}</span></template>
            <BaseChart :option="c.option" :dark="app.dark" height="240px" />
          </el-card>
        </el-col>
      </el-row>
    </template>

    <!-- 二级 IP 池 -->
    <template v-else-if="currentSite">
      <el-card shadow="never" class="mb">
        <div class="strip">
          <BaseCardCell v-for="c in siteInfoCells" :key="c.label" :label="c.label" :value="c.value" />
        </div>
      </el-card>

      <el-card shadow="never" class="mb">
        <div class="health-row">
          <div
            v-for="h in siteHealth"
            :key="h.label"
            class="health-item"
            :class="{ red: h.value > 0 }"
          >
            <span class="health-label">{{ h.label }}</span>
            <span class="health-value">{{ h.value }}</span>
          </div>
        </div>
      </el-card>

      <el-row :gutter="12">
        <el-col v-for="c in siteCharts" :key="c.title" :span="8" class="mb">
          <el-card shadow="never">
            <template #header><span>{{ c.title }}</span></template>
            <BaseChart :option="c.option" :dark="app.dark" height="240px" />
          </el-card>
        </el-col>
      </el-row>
    </template>

    <el-empty v-else description="等待数据..." />
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
.tabs {
  flex: 1;
}
.mb {
  margin-bottom: 12px;
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
.red {
  color: #f56c6c;
}
</style>
