import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api'
import type { Distributions, Overview, SiteSummary, Stats } from '../types'

export const useDataStore = defineStore('data', () => {
  const overview = ref<Overview | null>(null)
  const sites = ref<SiteSummary[]>([])
  const distributions = ref<Distributions | null>(null)
  const stats = ref<Stats | null>(null)
  const error = ref<string | null>(null)
  const lastUpdated = ref(0)
  let timer: number | undefined

  async function refresh() {
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
    } catch (e) {
      error.value = e instanceof Error ? e.message : '接口异常'
    }
  }

  function start() {
    if (timer !== undefined) return
    refresh()
    timer = window.setInterval(refresh, 5000)
  }

  function stop() {
    if (timer !== undefined) {
      clearInterval(timer)
      timer = undefined
    }
  }

  return { overview, sites, distributions, stats, error, lastUpdated, refresh, start, stop }
})
