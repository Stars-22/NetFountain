import { defineStore } from 'pinia'
import { ref } from 'vue'

const KEY = 'netfountain-refresh-interval'

// 可选自动刷新周期（秒），默认 5s
export const REFRESH_OPTIONS = [1, 2, 5, 10, 30, 60]

export const useRefreshStore = defineStore('refresh', () => {
  const saved = Number(localStorage.getItem(KEY))
  const intervalSec = ref(REFRESH_OPTIONS.includes(saved) ? saved : 5)

  function set(v: number) {
    intervalSec.value = v
    localStorage.setItem(KEY, String(v))
  }

  return { intervalSec, set }
})
