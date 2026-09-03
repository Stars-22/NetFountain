// NetFountain 前端面板 + 数据聚合后端 BFF（Express 5）。
// - 开发：npm run dev 由 concurrently 同时起本服务(3000)与 Vite(5173)
// - 生产：npm run build 后本服务托管 dist/ 并提供 /api，npm start 单命令启动

import express from 'express'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { config } from './config.js'
import { queryHistory, runRetention, db } from './db.js'
import { getState, startCollector } from './collector.js'
import { int, num, protoMap, latencyStats, avgRemainingSeconds } from './util.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const distDir = path.join(__dirname, '..', 'dist')

const ok = (data) => ({ code: 0, msg: 'ok', data })
const err = (code, msg) => ({ code, msg, data: null })

// ---- 时间范围 -> 降采样桶宽 ----
const RANGES = {
  '1h': 60,
  '6h': 300,
  '24h': 600,
  '7d': 3600,
}

function buildSiteSummaries() {
  const st = getState()
  // 直接遍历 state.sites（含 target_url/base_url 与 stale 标记），
  // 不依赖 st.proxy 是否存活，避免代理层抖动时站点列表整体消失
  const out = []
  for (const [name, entry] of Object.entries(st.sites)) {
    const d = entry.status
    if (!d) {
      out.push({
        name,
        target_url: entry.target_url || null,
        base_url: entry.base_url || null,
        reachable: false,
        stale: false,
        total: 0, leased_total: 0, free_total: 0,
        by_proto: { http: 0, https: 0, socks4: 0, socks5: 0 },
        avg_latency: null, pass_rate: null,
        errors: {}, drops: 0,
      })
      continue
    }
    const ps = d.pool_stats || {}
    const ls = latencyStats(entry.ips)
    const pulled = int(d.total_pulled) || 0
    const entered = int(d.total_entered) || 0
    out.push({
      name,
      target_url: entry.target_url || null,
      base_url: entry.base_url || null,
      reachable: true,
      stale: !!entry.stale,
      total: int(ps.total) || 0,
      leased_total: int(ps.leased_total) || 0,
      free_total: int(ps.free_total) || 0,
      by_proto: protoMap(ps.by_proto),
      avg_latency: ls.avg,
      pass_rate: pulled > 0 ? entered / pulled : null,
      errors: d.errors || {},
      drops: int(d.drops) || 0,
    })
  }
  return out
}

// 池级错误总数：errors 各项求和 + drops
function poolErrorsTotal(errors, drops) {
  let total = 0
  if (errors && typeof errors === 'object') {
    for (const v of Object.values(errors)) total += int(v) || 0
  }
  return total + (int(drops) || 0)
}

// /api/overview：按池分条返回（一级池 1 条 + 每个二级池 1 条），字段为各页所需超集
function buildOverview() {
  const st = getState()
  const now = Date.now() / 1000
  const l1 = st.level1

  let level1 = null
  if (l1) {
    const pulled = int(l1.total_pulled) || 0
    const entered = int(l1.total_entered) || 0
    level1 = {
      ip_count: int(l1.pool_size),
      uptime: num(l1.uptime),
      total_pulled: pulled,
      pass_rate: pulled > 0 ? entered / pulled : null,
      duplicate_rate: pulled > 0 ? (int(l1.total_duplicates) || 0) / pulled : null,
      by_proto: protoMap(l1.counts),
      errors: l1.errors || {},
      drops: int(l1.drops) || 0,
      errors_total: poolErrorsTotal(l1.errors, l1.drops),
      api_call_count: int(l1.api_call_count),
      avg_remaining: avgRemainingSeconds(st.level1Ips, now),
      stale: !!st.level1Stale,
    }
  }

  const sites = []
  for (const [name, entry] of Object.entries(st.sites)) {
    const d = entry.status
    if (!d) {
      sites.push({
        name,
        reachable: false,
        stale: false,
        target_url: entry.target_url || null,
        base_url: entry.base_url || null,
        ip_count: 0, free: 0, leased: 0,
        uptime: null, total_pulled: 0, pass_rate: null,
        avg_latency: null, by_proto: { http: 0, https: 0, socks4: 0, socks5: 0 },
        errors: {}, drops: 0, errors_total: 0,
        api_call_count: null, avg_remaining: null,
      })
      continue
    }
    const ps = d.pool_stats || {}
    const pulled = int(d.total_pulled) || 0
    const entered = int(d.total_entered) || 0
    sites.push({
      name,
      reachable: true,
      stale: !!entry.stale,
      target_url: entry.target_url || null,
      base_url: entry.base_url || null,
      ip_count: int(ps.total) || 0,
      free: int(ps.free_total) || 0,
      leased: int(ps.leased_total) || 0,
      uptime: num(d.uptime),
      total_pulled: pulled,
      pass_rate: pulled > 0 ? entered / pulled : null,
      avg_latency: latencyStats(entry.ips).avg,
      by_proto: protoMap(ps.by_proto),
      errors: d.errors || {},
      drops: int(d.drops) || 0,
      errors_total: poolErrorsTotal(d.errors, d.drops),
      api_call_count: int(d.api_call_count),
      avg_remaining: avgRemainingSeconds(entry.ips, now),
    })
  }

  return { updated_at: st.lastUpdatedAt, level1, sites }
}

// TTL 剩余分布分桶（剩余 = created_at + ttl - now；已过期归入 ≤1min）
const TTL_BUCKETS = [
  { name: '≤1min', min: 0, max: 60 },
  { name: '1~3min', min: 60, max: 180 },
  { name: '3~5min', min: 180, max: 300 },
  { name: '5~10min', min: 300, max: 600 },
  { name: '10~30min', min: 600, max: 1800 },
  { name: '30min~2h', min: 1800, max: 7200 },
  { name: '2h~6h', min: 7200, max: 21600 },
  { name: '6h~12h', min: 21600, max: 43200 },
  { name: '12h~24h', min: 43200, max: 86400 },
  { name: '≥24h', min: 86400, max: Infinity },
]

const LATENCY_BUCKETS = [
  { name: '<200ms', min: 0, max: 200 },
  { name: '200-500ms', min: 200, max: 500 },
  { name: '500-1000ms', min: 500, max: 1000 },
  { name: '1000-2000ms', min: 1000, max: 2000 },
  { name: '2000-3000ms', min: 2000, max: 3000 },
  { name: '≥3000ms', min: 3000, max: Infinity },
]

function ttlDistribution(ips, now) {
  const counts = TTL_BUCKETS.map((b) => ({ name: b.name, value: 0 }))
  let noTtl = 0
  if (Array.isArray(ips)) {
    for (const ip of ips) {
      // num(null) 会得到 0（Number(null)===0），永久/无TTL 必须先显式判空
      if (ip.ttl == null) {
        noTtl += 1
        continue
      }
      const ttl = num(ip.ttl)
      if (ttl === null) {
        noTtl += 1
        continue
      }
      const created = num(ip.created_at) || 0
      const remaining = Math.max(0, created + ttl - now)
      for (let i = 0; i < TTL_BUCKETS.length; i++) {
        const b = TTL_BUCKETS[i]
        if (remaining >= b.min && remaining < b.max) {
          counts[i].value += 1
          break
        }
      }
    }
  }
  return [...counts, { name: '永久/无TTL', value: noTtl }]
}

function latencyDistribution(ips) {
  const counts = LATENCY_BUCKETS.map((b) => ({ name: b.name, value: 0 }))
  if (!Array.isArray(ips)) return counts
  for (const ip of ips) {
    const l = num(ip.latency_ms)
    if (l === null) continue
    for (let i = 0; i < LATENCY_BUCKETS.length; i++) {
      const b = LATENCY_BUCKETS[i]
      if (l >= b.min && l < b.max) {
        counts[i].value += 1
        break
      }
    }
  }
  return counts
}

// /api/distributions：按池返回分布（level1 仅 TTL；二级池 TTL + 延迟）
function buildDistributions() {
  const st = getState()
  const now = Date.now() / 1000
  const pools = { level1: { ttl: ttlDistribution(st.level1Ips, now) } }
  for (const [name, s] of Object.entries(st.sites)) {
    pools[name] = {
      ttl: ttlDistribution(s.ips, now),
      latency: latencyDistribution(s.ips),
    }
  }
  return { updated_at: st.lastUpdatedAt, pools }
}

function buildStats() {
  const st = getState()
  const l1 = st.level1
  const proxy = st.proxy
  const sites = buildSiteSummaries()
  return {
    level1: l1
      ? {
          pool_size: int(l1.pool_size),
          total_pulled: int(l1.total_pulled),
          total_entered: int(l1.total_entered),
          total_duplicates: int(l1.total_duplicates),
          errors: l1.errors || {},
          drops: int(l1.drops) || 0,
        }
      : null,
    sites,
    proxy: proxy
      ? {
          uptime: num(proxy.uptime),
          started_at: proxy.started_at || null,
          total_calls: proxy.stats ? proxy.stats.total_calls : 0,
          calls_by_ip: proxy.stats ? proxy.stats.calls_by_ip || {} : {},
          calls_by_site: proxy.stats ? proxy.stats.calls_by_site || {} : {},
          errors: proxy.stats ? proxy.stats.errors || {} : {},
        }
      : null,
    updated_at: st.lastUpdatedAt,
  }
}

function allLevel2Ips() {
  const st = getState()
  const list = []
  for (const [name, s] of Object.entries(st.sites)) {
    if (!Array.isArray(s.ips)) continue
    for (const ip of s.ips) {
      list.push({
        site: name,
        proxy_url: ip.proxy_url || '',
        protocol: ip.protocol || '',
        region: ip.region || null,
        latency_ms: num(ip.latency_ms),
        leased: !!ip.leased,
        ttl: ip.ttl == null ? null : num(ip.ttl),  // num(null)===0，永久项需保留 null
        created_at: num(ip.created_at),
      })
    }
  }
  return list
}

// ---- Express ----
const app = express()
app.use(express.json())

app.get('/api/health', (req, res) => res.json(ok({ status: 'ok', now: Date.now() })))
app.get('/api/overview', (req, res) => res.json(ok(buildOverview())))
app.get('/api/distributions', (req, res) => res.json(ok(buildDistributions())))
app.get('/api/stats', (req, res) => res.json(ok(buildStats())))
app.get('/api/sites', (req, res) => res.json(ok(buildSiteSummaries())))

app.get('/api/ips', (req, res) => {
  const protocol = String(req.query.protocol || '')
  const status = String(req.query.status || '')
  const site = String(req.query.site || '')
  const page = Math.max(1, int(req.query.page) || 1)
  const size = Math.min(200, Math.max(1, int(req.query.size) || 20))

  let items = allLevel2Ips()
  if (protocol) items = items.filter((i) => i.protocol === protocol)
  if (status === 'free') items = items.filter((i) => !i.leased)
  if (status === 'leased') items = items.filter((i) => i.leased)
  if (site) items = items.filter((i) => i.site === site)

  const total = items.length
  const start = (page - 1) * size
  res.json(ok({ total, page, size, items: items.slice(start, start + size) }))
})

app.get('/api/sites/:site/ips', (req, res) => {
  const st = getState()
  const s = st.sites[req.params.site]
  if (!s) return res.json(err(40400, `site not found: ${req.params.site}`))
  const items = (s.ips || []).map((ip) => ({
    proxy_url: ip.proxy_url || '',
    protocol: ip.protocol || '',
    region: ip.region || null,
    latency_ms: num(ip.latency_ms),
    leased: !!ip.leased,
    ttl: num(ip.ttl),
    created_at: num(ip.created_at),
  }))
  res.json(ok(items))
})

app.get('/api/sites/:site/status', (req, res) => {
  const st = getState()
  const s = st.sites[req.params.site]
  if (!s) return res.json(err(40400, `site not found: ${req.params.site}`))
  res.json(ok(s.status))
})

// 一键释放站点全部租赁 IP：BFF 代理转发上游 release-all（前端禁止直连二级池）
app.post('/api/sites/:site/release-all', async (req, res) => {
  const st = getState()
  // 优先用采集缓存的 base_url（含 proxy 掉线期间），回退到代理层 health 站点表
  const cached = st.sites[req.params.site]
  const entry =
    cached && cached.base_url
      ? { base_url: cached.base_url }
      : (st.proxy && st.proxy.sites ? st.proxy.sites : []).find(
          (s) => s.name === req.params.site,
        )
  if (!entry || !entry.base_url) {
    return res.json(err(40400, `site not found: ${req.params.site}`))
  }
  const base = entry.base_url.replace(/\/+$/, '')
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 5000)
  try {
    const r = await fetch(`${base}/api/v1/ips/release-all`, {
      method: 'POST',
      signal: controller.signal,
    })
    let body
    try {
      body = await r.json()
    } catch {
      body = err(50200, `invalid upstream response: HTTP ${r.status}`)
    }
    res.json(body)
  } catch (e) {
    res.json(err(50200, `upstream error: ${e && e.message ? e.message : e}`))
  } finally {
    clearTimeout(timer)
  }
})

// /api/history 结果缓存：queryHistory 为全量窗口聚合（同步、可达秒级），
// 图表页每个刷新 tick 都会请求，按 range 缓存 Promise（防并发击穿），
// TTL 内直接复用，避免重算反复阻塞事件循环导致其他接口排队超时
const HISTORY_CACHE_MS = Number(process.env.HISTORY_CACHE_MS || 30000)
const historyCache = new Map() // range -> { at, promise }

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

// ---- 生产静态托管 + SPA fallback ----
if (fs.existsSync(distDir)) {
  app.use(express.static(distDir))
  app.use((req, res, next) => {
    if (req.method !== 'GET' || req.path.startsWith('/api')) return next()
    res.sendFile(path.join(distDir, 'index.html'))
  })
}

// ---- 每日 0 点数据清理 ----
function scheduleRetention() {
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

// ---- 启动 ----
startCollector()
scheduleRetention()

app.listen(config.port, () => {
  console.log(`[bff] NetFountain BFF listening on http://localhost:${config.port}`)
  console.log(`[bff] level1=${config.level1Url} proxy=${config.proxyUrl}`)
  console.log(`[bff] db=${config.dbFile} collect=${config.collectIntervalMs}ms snapshot=${config.snapshotIntervalMs}ms`)
})

// 进程退出时优雅关闭
function shutdown() {
  try {
    db.close()
  } catch {}
  process.exit(0)
}
process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
