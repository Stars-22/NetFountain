<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { fmtMs, fmtRemaining, latencyType } from '../format'
import { useDataStore } from '../stores/data'
import type { IpItem } from '../types'

const data = useDataStore()

const protocol = ref('')
const status = ref('')
const site = ref('')
const page = ref(1)
const size = ref(20)

const items = ref<IpItem[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)

const protocols = ['http', 'https', 'socks4', 'socks5']

async function load() {
  loading.value = true
  error.value = null
  try {
    const res = await api.ips({
      protocol: protocol.value,
      status: status.value,
      site: site.value,
      page: page.value,
      size: size.value,
    })
    items.value = res.items
    total.value = res.total
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

watch([protocol, status, site], () => {
  page.value = 1
  load()
})
watch(page, load)
watch(size, () => {
  page.value = 1
  load()
})

let timer: number | undefined

onMounted(() => {
  load()
  // 5s 自动刷新，保持「剩余时间」等实时字段新鲜
  timer = window.setInterval(() => {
    if (!loading.value) load()
  }, 5000)
})

onBeforeUnmount(() => {
  if (timer !== undefined) clearInterval(timer)
})

const siteOptions = computed(() => data.sites.map((s) => ({ value: s.name, label: s.name })))
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="toolbar">
        <div class="filters">
          <el-select v-model="protocol" placeholder="协议" clearable style="width: 140px">
            <el-option v-for="p in protocols" :key="p" :value="p" :label="p" />
          </el-select>
          <el-select v-model="status" placeholder="状态" clearable style="width: 140px">
            <el-option value="free" label="空闲" />
            <el-option value="leased" label="租赁中" />
          </el-select>
          <el-select v-model="site" placeholder="站点" clearable style="width: 160px">
            <el-option v-for="s in siteOptions" :key="s.value" :value="s.value" :label="s.label" />
          </el-select>
        </div>
        <el-button @click="load">刷新</el-button>
      </div>
    </template>

    <el-alert v-if="error" :title="'加载失败：' + error" type="error" show-icon :closable="false" class="mb" />

    <el-table :data="items" v-loading="loading" stripe>
      <el-table-column prop="site" label="站点" width="120" />
      <el-table-column prop="proxy_url" label="代理地址" min-width="240" show-overflow-tooltip />
      <el-table-column label="协议" width="90">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ row.protocol }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="地区" width="90">
        <template #default="{ row }">{{ row.region || '-' }}</template>
      </el-table-column>
      <el-table-column label="延迟" width="110" sortable :sort-method="(a: IpItem, b: IpItem) => (a.latency_ms ?? -1) - (b.latency_ms ?? -1)">
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
      <el-table-column label="剩余时间" width="110">
        <template #default="{ row }">{{ fmtRemaining(row) }}</template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="size"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
      />
    </div>
  </el-card>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.filters {
  display: flex;
  gap: 12px;
}
.mb {
  margin-bottom: 12px;
}
.pager {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
