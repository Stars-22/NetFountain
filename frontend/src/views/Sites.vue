<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { barChart } from '../charts'
import BaseChart from '../components/BaseChart.vue'
import { fmtInt, fmtMs, fmtPct, latencyType } from '../format'
import { useAppStore } from '../stores/app'
import { useDataStore } from '../stores/data'
import type { IpItem, SiteSummary } from '../types'

const data = useDataStore()
const app = useAppStore()

const activeTab = ref('')
const siteIps = ref<IpItem[]>([])
const loading = ref(false)
const ipError = ref<string | null>(null)

const sites = computed(() => data.sites)

watch(
  () => data.sites,
  (list) => {
    if (!activeTab.value && list.length) activeTab.value = list[0].name
  },
  { immediate: true },
)

async function loadSiteIps(name: string) {
  if (!name) return
  loading.value = true
  ipError.value = null
  try {
    siteIps.value = await api.siteIps(name)
  } catch (e) {
    ipError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

watch(activeTab, (name) => loadSiteIps(name))

function currentSite(): SiteSummary | null {
  return sites.value.find((s) => s.name === activeTab.value) || null
}

const current = computed(() => currentSite())

const protoOption = computed(() => {
  const p = current.value?.by_proto
  const names = ['http', 'https', 'socks4', 'socks5']
  const colors = ['#409EFF', '#67C23A', '#E6A23C', '#9C27B0']
  return barChart(
    names.map((n, i) => ({ name: n, value: p ? p[n as keyof typeof p] : 0, color: colors[i] })),
  )
})

const errorItems = computed(() => {
  const e = current.value?.errors || {}
  return [
    { label: '同步失败', value: e.sync_failures || 0 },
    { label: '测试失败', value: e.test_failures || 0 },
    { label: '复验失败', value: e.revalidate_failures || 0 },
    { label: 'TTL 清扫失败', value: e.ttl_sweep_failures || 0 },
    { label: '空池租赁', value: e.empty_acquires || 0 },
    { label: '丢弃批次', value: current.value?.drops || 0 },
  ]
})
</script>

<template>
  <el-card shadow="never">
    <el-tabs v-model="activeTab" type="card">
      <el-tab-pane v-for="s in sites" :key="s.name" :name="s.name">
        <template #label>
          <span>
            {{ s.name }}
            <el-tag :type="s.reachable ? 'success' : 'danger'" size="small" class="tab-tag">
              {{ s.reachable ? '在线' : '离线' }}
            </el-tag>
          </span>
        </template>
      </el-tab-pane>
    </el-tabs>

    <template v-if="current">
      <el-row :gutter="12" class="mb">
        <el-col :span="4">
          <el-card shadow="hover">
            <div class="label">总数</div>
            <div class="value">{{ fmtInt(current.total) }}</div>
          </el-card>
        </el-col>
        <el-col :span="4">
          <el-card shadow="hover">
            <div class="label">空闲</div>
            <div class="value green">{{ fmtInt(current.free_total) }}</div>
          </el-card>
        </el-col>
        <el-col :span="4">
          <el-card shadow="hover">
            <div class="label">租赁中</div>
            <div class="value orange">{{ fmtInt(current.leased_total) }}</div>
          </el-card>
        </el-col>
        <el-col :span="4">
          <el-card shadow="hover">
            <div class="label">平均延迟</div>
            <div class="value">{{ fmtMs(current.avg_latency) }}</div>
          </el-card>
        </el-col>
        <el-col :span="4">
          <el-card shadow="hover">
            <div class="label">测试通过率</div>
            <div class="value">{{ fmtPct(current.pass_rate) }}</div>
          </el-card>
        </el-col>
        <el-col :span="4">
          <el-card shadow="hover">
            <div class="label">目标站点</div>
            <div class="value small">{{ current.target_url || '-' }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="12" class="mb">
        <el-col :span="12">
          <el-card shadow="never">
            <template #header><span>协议分布</span></template>
            <BaseChart :option="protoOption" :dark="app.dark" height="240px" />
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never">
            <template #header><span>健康指标</span></template>
            <el-descriptions :column="2" border>
              <el-descriptions-item v-for="e in errorItems" :key="e.label" :label="e.label">
                <span :class="{ red: e.value > 0 }">{{ e.value }}</span>
              </el-descriptions-item>
            </el-descriptions>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never">
        <template #header><span>站点 IP（{{ siteIps.length }}）</span></template>
        <el-alert v-if="ipError" :title="'加载失败：' + ipError" type="error" show-icon :closable="false" class="mb" />
        <el-table :data="siteIps" v-loading="loading" stripe max-height="480">
          <el-table-column prop="proxy_url" label="代理地址" min-width="240" show-overflow-tooltip />
          <el-table-column label="协议" width="90">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ row.protocol }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="地区" width="90">
            <template #default="{ row }">{{ row.region || '-' }}</template>
          </el-table-column>
          <el-table-column label="延迟" width="110">
            <template #default="{ row }">
              <el-tag :type="latencyType(row.latency_ms)" size="small">{{ fmtMs(row.latency_ms) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.leased ? 'warning' : 'success'" size="small">
                {{ row.leased ? '租赁中' : '空闲' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <el-empty v-else description="暂无站点" />
  </el-card>
</template>

<style scoped>
.mb {
  margin-bottom: 12px;
}
.tab-tag {
  margin-left: 6px;
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
.value.small {
  font-size: 13px;
  font-weight: 400;
}
.green {
  color: #67c23a;
}
.orange {
  color: #e6a23c;
}
.red {
  color: #f56c6c;
}
</style>
