<script setup lang="ts">
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  option: echarts.EChartsOption
  height?: string
  dark?: boolean
}>()

const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null

function init() {
  if (!el.value) return
  chart?.dispose()
  chart = echarts.init(el.value, props.dark ? 'dark' : undefined)
  chart.setOption(props.option)
}

function resize() {
  chart?.resize()
}

onMounted(() => {
  init()
  window.addEventListener('resize', resize)
})

watch(
  () => props.option,
  () => {
    if (chart) chart.setOption(props.option, true)
  },
  { deep: true },
)

watch(
  () => props.dark,
  () => init(),
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
})
</script>

<template>
  <div ref="el" :style="{ width: '100%', height: height || '300px' }"></div>
</template>
