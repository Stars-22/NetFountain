// 数据保留定时清理：每日 0 点执行一次 retention（删除超过保留天数的行）。

import { runRetention } from './db.js'

export function scheduleRetention() {
  const now = new Date()
  const nextMidnight = new Date(now)
  nextMidnight.setHours(24, 0, 0, 0)
  const ms = nextMidnight.getTime() - now.getTime()
  setTimeout(() => {
    try {
      const removed = runRetention()
      console.log(`[bff] retention cleaned ${removed} rows`)
    } catch (e) {
      console.error('[bff] retention failed:', e && e.message)
    }
    setInterval(() => {
      try {
        runRetention()
      } catch (e) {
        console.error('[bff] retention failed:', e && e.message)
      }
    }, 86400000)
  }, ms)
}
