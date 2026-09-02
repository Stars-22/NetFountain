<script setup lang="ts">
defineProps<{
  label: string
  value: string
}>()
</script>

<template>
  <div class="cell">
    <div class="cell-label">{{ label }}</div>
    <div class="cell-value">{{ value }}</div>
  </div>
</template>

<style scoped>
.cell {
  min-width: 110px;
  padding: 0 16px;
  border-right: 1px solid var(--el-border-color-lighter);
}
.cell:last-child {
  border-right: none;
}
.cell-label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
}
.cell-value {
  font-size: 18px;
  font-weight: 600;
  margin-top: 4px;
  white-space: nowrap;
}
</style>
