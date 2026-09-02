import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api'
import { useRefreshStore } from './refresh'
import type { Distributions, Overview, SiteSummary, Stats } from '../types'

export const useDataStore = defineStore('data', () => {
  const refresh = useRefreshStore()
  const overview = ref<Overview | null>(null)
  const sites = ref<SiteSummary[]>([])
  const distributions = ref<Distributions | null>(null)
  const stats = ref<Stats | null>(null)
  const error = ref<string | null>(null)
  const lastUpdated = ref(0)

  // 页面级刷新钩子：每个轮询 tick 在核心数据刷新后触发（用于刷新历史图表等页面数据）
  const hooks = new Set<() => void>()
  let timer: number | undefined

  function addHook(fn: () => void): () => void {
    hooks.add(fn)
    return () => {
      hooks.delete(fn)
    }
  }

  async function refreshOnce() {
    try {
      const [ov, st, dist] = await Promise.all([
        api.overview(),
        api.sites(),
        api.distributions(),
      ])
      overview.value = ov
      sites.value = st
      distributions.value = dist
      stats.value = await api.stats()
      error.value = null
      lastUpdated.value = Date.now()
      for (const fn of hooks) {
        try {
          fn()
        } catch {
          // 单个钩子异常不影响整体刷新
        }
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : '接口异常'
    }
  }

  function start() {
    if (timer !== undefined) return
    refreshOnce()
    timer = window.setInterval(refreshOnce, refresh.intervalSec * 1000)
  }

  // 刷新周期变化后重启轮询，并立即触发一次刷新
  function restart() {
    stop()
    start()
  }

  function stop() {
    if (timer !== undefined) {
      clearInterval(timer)
      timer = undefined
    }
  }

  return {
    overview,
    sites,
    distributions,
    stats,
    error,
    lastUpdated,
    refreshOnce,
    addHook,
    start,
    restart,
    stop,
  }
})
