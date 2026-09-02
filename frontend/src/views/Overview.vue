<script setup lang="ts">
import { computed } from 'vue'
import BaseCardCell from '../components/BaseCardCell.vue'
import RefreshSelector from '../components/RefreshSelector.vue'
import { fmtDuration, fmtInt, fmtMs, fmtPct } from '../format'
import { useDataStore } from '../stores/data'
import type { Level1Strip, SiteStrip } from '../types'

const data = useDataStore()

interface Cell {
  label: string
  value: string
}

const l1Cells = computed<Cell[]>(() => {
  const l1: Level1Strip | null = data.overview?.level1 ?? null
  return [
    { label: 'IP 数', value: fmtInt(l1?.ip_count) },
    { label: '启动时长', value: fmtDuration(l1?.uptime) },
    { label: '总拉取数量', value: fmtInt(l1?.total_pulled) },
    { label: '测试通过率', value: fmtPct(l1?.pass_rate) },
    { label: '重复率', value: fmtPct(l1?.duplicate_rate) },
    { label: '错误总数', value: fmtInt(l1?.errors_total) },
    { label: 'API 调用次数', value: fmtInt(l1?.api_call_count) },
    { label: '平均剩余时间', value: fmtDuration(l1?.avg_remaining) },
  ]
})

function siteCells(s: SiteStrip): Cell[] {
  return [
    { label: 'IP 数', value: fmtInt(s.ip_count) },
    { label: '可用 IP', value: fmtInt(s.free) },
    { label: '租赁中', value: fmtInt(s.leased) },
    { label: '测试通过率', value: fmtPct(s.pass_rate) },
    { label: '平均延迟', value: fmtMs(s.avg_latency) },
    { label: '错误总数', value: fmtInt(s.errors_total) },
    { label: 'API 调用次数', value: fmtInt(s.api_call_count) },
    { label: '平均剩余时间', value: fmtDuration(s.avg_remaining) },
  ]
}

const siteStrips = computed<SiteStrip[]>(() => data.overview?.sites ?? [])
</script>

<template>
  <div>
    <div class="toolbar">
      <RefreshSelector />
    </div>

    <el-alert
      v-if="!data.overview && !data.error"
      title="等待数据..."
      type="info"
      :closable="false"
      class="mb"
    />

    <el-card v-if="data.overview?.level1" shadow="hover" class="mb">
      <template #header><span>一级 IP 池</span></template>
      <div class="strip">
        <BaseCardCell v-for="c in l1Cells" :key="c.label" :label="c.label" :value="c.value" />
      </div>
    </el-card>

    <el-card v-for="s in siteStrips" :key="s.name" shadow="hover" class="mb">
      <template #header>
        <div class="card-header">
          <span>二级 IP 池 · {{ s.name }}</span>
          <el-tag :type="s.reachable ? 'success' : 'danger'" size="small">
            {{ s.reachable ? '在线' : '离线' }}
          </el-tag>
        </div>
      </template>
      <div class="strip">
        <BaseCardCell v-for="c in siteCells(s)" :key="c.label" :label="c.label" :value="c.value" />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}
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
</style>
