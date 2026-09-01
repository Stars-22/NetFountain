// NetFountain 前端面板 + 数据聚合后端 BFF（Express 5）。
// - 开发：npm run dev 由 concurrently 同时起本服务(3000)与 Vite(5173)
// - 生产：npm run build 后本服务托管 dist/ 并提供 /api，npm start 单命令启动

import express from 'express'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { config } from './config.js'
import { getLatestRate, queryHistory, runRetention, db } from './db.js'
import { getState, startCollector } from './collector.js'
import { int, num, protoMap, latencyStats } from './util.js'

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
  const sites = st.proxy ? st.proxy.sites || [] : []
  const out = []
  for (const s of sites) {
    const name = s.name
    const data = st.sites[name]
    if (!data || !data.status) {
      out.push({
        name,
        target_url: s.target_url || null,
        base_url: s.base_url || null,
        reachable: false,
        total: 0, leased_total: 0, free_total: 0,
        by_proto: { http: 0, https: 0, socks4: 0, socks5: 0 },
        avg_latency: null, pass_rate: null,
        errors: {}, drops: 0,
      })
      continue
    }
    const d = data.status
    const ps = d.pool_stats || {}
    const ls = latencyStats(data.ips)
    const pulled = int(d.total_pulled) || 0
    const entered = int(d.total_entered) || 0
    out.push({
      name,
      target_url: s.target_url || null,
      base_url: s.base_url || null,
      reachable: true,
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

function buildOverview() {
  const st = getState()
  const l1 = st.level1
  const summaries = buildSiteSummaries()

  let available = 0
  let leased = 0
  let latSum = 0
  let latN = 0
  const byProto = { http: 0, https: 0, socks4: 0, socks5: 0 }
  const errTotals = { pull_failures: 0, test_failures: 0, sync_failures: 0, revalidate_failures: 0, ttl_sweep_failures: 0, empty_acquires: 0, drops: 0 }

  for (const s of summaries) {
    available += s.free_total
    leased += s.leased_total
    if (s.avg_latency != null) {
      const total = s.total
      latSum += s.avg_latency * (total || 1)
      latN += total || 1
    }
    byProto.http += s.by_proto.http
    byProto.https += s.by_proto.https
    byProto.socks4 += s.by_proto.socks4
    byProto.socks5 += s.by_proto.socks5
    const e = s.errors || {}
    errTotals.sync_failures += int(e.sync_failures) || 0
    errTotals.test_failures += int(e.test_failures) || 0
    errTotals.revalidate_failures += int(e.revalidate_failures) || 0
    errTotals.ttl_sweep_failures += int(e.ttl_sweep_failures) || 0
    errTotals.empty_acquires += int(e.empty_acquires) || 0
    errTotals.drops += s.drops
  }

  let poolCapacity = null
  let totalPulled = 0
  let totalEntered = 0
  let totalDuplicates = 0
  if (l1) {
    poolCapacity = int(l1.pool_size)
    totalPulled = int(l1.total_pulled) || 0
    totalEntered = int(l1.total_entered) || 0
    totalDuplicates = int(l1.total_duplicates) || 0
    const e = l1.errors || {}
    errTotals.pull_failures += int(e.pull_failures) || 0
    errTotals.test_failures += int(e.test_failures) || 0
    errTotals.ttl_sweep_failures += int(e.ttl_sweep_failures) || 0
    errTotals.drops += int(l1.drops) || 0
  }

  // 代理层错误
  const proxyStats = st.proxy ? st.proxy.stats || {} : {}
  let proxyErrors = 0
  if (proxyStats.errors && typeof proxyStats.errors === 'object') {
    for (const v of Object.values(proxyStats.errors)) proxyErrors += int(v) || 0
  }

  const pullRate = getLatestRate('level1')
  const errorsTotal =
    errTotals.pull_failures + errTotals.test_failures + errTotals.sync_failures +
    errTotals.revalidate_failures + errTotals.ttl_sweep_failures +
    errTotals.empty_acquires + proxyErrors

  return {
    pool_capacity: poolCapacity,
    available_count: available,
    leased_count: leased,
    avg_latency: latN > 0 ? latSum / latN : null,
    by_proto: byProto,
    pull_rate: pullRate,
    pass_rate: totalPulled > 0 ? totalEntered / totalPulled : null,
    duplicate_rate: totalPulled > 0 ? totalDuplicates / totalPulled : null,
    errors_total: errorsTotal,
    errors: errTotals,
    proxy_errors: proxyErrors,
    updated_at: st.lastUpdatedAt,
  }
}

function buildDistributions() {
  const st = getState()
  const latencyBuckets = [
    { name: '<200ms', min: 0, max: 200 },
    { name: '200-500ms', min: 200, max: 500 },
    { name: '500-1000ms', min: 500, max: 1000 },
    { name: '1000-2000ms', min: 1000, max: 2000 },
    { name: '2000-3000ms', min: 2000, max: 3000 },
    { name: '≥3000ms', min: 3000, max: Infinity },
  ]
  const latencyCounts = latencyBuckets.map((b) => ({ name: b.name, value: 0 }))

  const ttlBuckets = [
    { name: '<1min', min: 0, max: 60 },
    { name: '1-5min', min: 60, max: 300 },
    { name: '5-30min', min: 300, max: 1800 },
    { name: '30min-1h', min: 1800, max: 3600 },
    { name: '1-24h', min: 3600, max: 86400 },
    { name: '≥24h', min: 86400, max: Infinity },
  ]
  const ttlCounts = ttlBuckets.map((b) => ({ name: b.name, value: 0 }))
  let noTtl = 0

  const now = Date.now() / 1000

  const scan = (ips, hasLatency) => {
    if (!Array.isArray(ips)) return
    for (const ip of ips) {
      if (hasLatency) {
        const l = num(ip.latency_ms)
        if (l !== null) {
          for (let i = 0; i < latencyBuckets.length; i++) {
            const b = latencyBuckets[i]
            if (l >= b.min && l < b.max) { latencyCounts[i].value += 1; break }
          }
        }
      }
      const ttl = num(ip.ttl)
      const created = num(ip.created_at)
      if (ttl === null) {
        noTtl += 1
        continue
      }
      const remaining = (created || 0) + ttl - now
      if (remaining <= 0) continue
      for (let i = 0; i < ttlBuckets.length; i++) {
        const b = ttlBuckets[i]
        if (remaining >= b.min && remaining < b.max) { ttlCounts[i].value += 1; break }
      }
    }
  }

  for (const s of Object.values(st.sites)) scan(s.ips, true)
  scan(st.level1Ips, false)

  return {
    latency: latencyCounts,
    ttl: [...ttlCounts, { name: '永久/无TTL', value: noTtl }],
    updated_at: st.lastUpdatedAt,
  }
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
          total_calls: proxy.stats ? proxy.stats.total_calls : 0,
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
        ttl: num(ip.ttl),
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

app.get('/api/history', (req, res) => {
  const range = String(req.query.range || '24h')
  const bucketSec = RANGES[range]
  if (!bucketSec) return res.json(err(40000, `invalid range: ${range}`))
  const sinceTs = Math.floor(Date.now() / 1000) - bucketSec * 200
  const series = queryHistory(bucketSec, sinceTs)
  res.json(ok({ range, bucketSec, series }))
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
