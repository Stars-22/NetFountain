// /api/history：按 range 缓存 Promise 的历史查询路由。
// queryHistory 为全量窗口聚合（同步、可达秒级），图表页每个刷新 tick 都会请求，
// 按 range 缓存 Promise（防并发击穿），TTL 内直接复用，避免重算反复阻塞事件循环
// 导致其他接口排队超时。

import { RANGES } from '../buckets.js'
import { err, ok } from '../response.js'
import { queryHistory } from '../db.js'

const HISTORY_CACHE_MS = Number(process.env.HISTORY_CACHE_MS || 30000)
const historyCache = new Map() // range -> { at, promise }

export function registerHistoryRoute(app) {
  app.get('/api/history', async (req, res) => {
    const range = String(req.query.range || '24h')
    const bucketSec = RANGES[range]
    if (!bucketSec) return res.json(err(40000, `invalid range: ${range}`))
    const now = Date.now()
    let entry = historyCache.get(range)
    if (!entry || now - entry.at >= HISTORY_CACHE_MS) {
      entry = {
        at: now,
        promise: queryHistory(bucketSec, Math.floor(Date.now() / 1000) - bucketSec * 200),
      }
      historyCache.set(range, entry)
    }
    try {
      const series = await entry.promise
      res.json(ok({ range, bucketSec, series }))
    } catch (e) {
      historyCache.delete(range)
      res.json(err(50000, `history query failed: ${e && e.message ? e.message : e}`))
    }
  })
}
