<script setup lang="ts">
import { REFRESH_OPTIONS, useRefreshStore } from '../stores/refresh'
import { useDataStore } from '../stores/data'

const refresh = useRefreshStore()
const data = useDataStore()

function onChange(v: number) {
  refresh.set(v)
  data.restart()
}
</script>

<template>
  <span class="refresh-selector">
    <span class="label">自动刷新</span>
    <el-select
      :model-value="refresh.intervalSec"
      size="small"
      style="width: 88px"
      @change="onChange"
    >
      <el-option v-for="o in REFRESH_OPTIONS" :key="o" :value="o" :label="o + 's'" />
    </el-select>
  </span>
</template>

<style scoped>
.refresh-selector {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
