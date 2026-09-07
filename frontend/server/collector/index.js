// 采集循环：自调度 async 循环，每 collectIntervalMs 并发请求 NetFountain 服务，
// 解析 {code,msg,data}，code!==0 记录失败并跳过，不中断循环。
// 单次请求带 AbortController 超时（fetchTimeoutMs，见 fetcher.js），
// 无阻塞式长耗时操作。
// 瞬时失败采用 last-known-good 策略：连续 offlineAfterFailures 个周期失败才把
// 对应数据置空（真正离线），期间保留上次成功数据并标记 stale（前端显示"数据延迟"）。

import { config } from '../config.js'
import { saveMetrics, saveSnapshots } from '../db.js'
import { buildSnapshots, globalMetricsRow, level1MetricsRow, siteMetricsRow } from './metrics.js'
import { fetchJson, isOk } from './fetcher.js'

// 内存态：服务实时数据缓存，供 /api/ips、/api/distributions、/api/overview 直接读取
const state = {
  level1: null, // 一级池 /status data（last-known-good）
  level1Stale: false, // 一级池数据是否为失败期间保留的旧值
  level1Ips: null, // 一级池 /ips data（last-known-good）
  proxy: null, // 代理层 /health data（last-known-good）
  // name -> { status, ips, stale, target_url, base_url }
  // status 为 null 表示该站点连续失败达阈值（真正离线）；
  // stale=true 表示本次采集失败但沿用上次数据展示
  sites: {},
  lastUpdatedAt: 0,
}

// 各数据源连续失败计数：level1 两个接口任一失败即计一次，成功（全部 ok）清零；
// 站点同理（status+ips 任一失败即计）。达到 offlineAfterFailures 才置空数据。
const failCount = { level1: 0, proxy: 0, sites: {} } // sites: name -> count

// 上次成功 health 的站点表：health 短暂失败时沿用其继续采集各站点，
// 各站点按自身连通性独立判定，避免代理层抖动导致全站闪烁离线
let lastSiteList = []

let lastSnapshotTs = 0
let running = false
let timer = null

export function getState() {
  return state
}

async function collectOnce() {
  const level1Base = config.level1Url.replace(/\/+$/, '')
  const proxyBase = config.proxyUrl.replace(/\/+$/, '')
  const maxFailures = Math.max(1, config.offlineAfterFailures)

  const [l1Status, l1Ips, health] = await Promise.all([
    fetchJson(`${level1Base}/api/v1/status`),
    fetchJson(`${level1Base}/api/v1/ips`),
    fetchJson(`${proxyBase}/api/v1/health`),
  ])

  // 一级池：两个接口全部成功才更新；任一失败计一次失败，
  // 未达阈值保留旧值并标记 stale，达到阈值才置空（真正离线）
  if (isOk(l1Status) && isOk(l1Ips)) {
    state.level1 = l1Status.data
    state.level1Ips = l1Ips.data
    failCount.level1 = 0
    state.level1Stale = false
  } else {
    failCount.level1 += 1
    if (failCount.level1 >= maxFailures) {
      state.level1 = null
      state.level1Ips = null
      state.level1Stale = false
    } else {
      state.level1Stale = state.level1 != null
    }
  }

  // 代理层 health：成功更新并刷新站点表；失败沿用上次站点表继续采集，
  // 连续失败达阈值置空 proxy（Stats 页代理层数据随之显示为空）
  if (isOk(health)) {
    state.proxy = health.data
    failCount.proxy = 0
    lastSiteList = health.data.sites || []
  } else {
    failCount.proxy += 1
    if (failCount.proxy >= maxFailures) state.proxy = null
  }

  const siteResults = await Promise.all(
    lastSiteList.map(async (s) => {
      const base = (s.base_url || '').replace(/\/+$/, '')
      const name = s.name
      if (!base) return { name, target_url: s.target_url || null, base_url: null, status: null, ips: null, ok: false }
      const [st, ips] = await Promise.all([
        fetchJson(`${base}/api/v1/status`),
        fetchJson(`${base}/api/v1/ips`),
      ])
      return {
        name,
        target_url: s.target_url || null,
        base_url: base,
        status: isOk(st) ? st.data : null,
        ips: isOk(ips) ? ips.data : null,
        ok: isOk(st) && isOk(ips),
      }
    }),
  )

  // 站点：全部接口成功才更新；失败计一次，未达阈值保留旧值 + stale，
  // 达到阈值（或从未成功过）置 status=null（真正离线）。
  // 以 siteResults 重建整个映射，同时完成对已删除站点的剪枝
  const sites = {}
  for (const r of siteResults) {
    if (r.ok) {
      sites[r.name] = {
        status: r.status,
        ips: r.ips,
        stale: false,
        target_url: r.target_url,
        base_url: r.base_url,
      }
      failCount.sites[r.name] = 0
    } else {
      const n = (failCount.sites[r.name] || 0) + 1
      failCount.sites[r.name] = n
      const prev = state.sites[r.name]
      if (n >= maxFailures || !prev || prev.status == null) {
        sites[r.name] = {
          status: null,
          ips: null,
          stale: false,
          target_url: r.target_url,
          base_url: r.base_url,
        }
      } else {
        sites[r.name] = {
          status: prev.status,
          ips: prev.ips,
          stale: true,
          target_url: r.target_url,
          base_url: r.base_url,
        }
      }
    }
  }
  state.sites = sites
  state.lastUpdatedAt = Date.now()

  const now = Math.floor(Date.now() / 1000)
  const siteRows = []
  for (const [name, s] of Object.entries(sites)) {
    if (s.status) siteRows.push(siteMetricsRow(now, name, s.status, s.ips))
  }
  const l1Row = level1MetricsRow(now, state.level1)
  const rows = []
  if (l1Row) rows.push(l1Row)
  rows.push(...siteRows)
  // global 行的计数列全部派生自一级池，一级池不可达时跳过，避免写入伪归零值
  if (l1Row) rows.push(globalMetricsRow(now, l1Row, siteRows))

  try {
    saveMetrics(rows)
  } catch (e) {
    console.error('[collector] saveMetrics failed:', e && e.message)
  }

  if (now - lastSnapshotTs >= config.snapshotIntervalMs / 1000) {
    lastSnapshotTs = now
    try {
      saveSnapshots(buildSnapshots(now, state))
    } catch (e) {
      console.error('[collector] saveSnapshots failed:', e && e.message)
    }
  }
}

function loop() {
  if (!running) return
  collectOnce()
    .catch((e) => console.error('[collector] tick failed:', e && e.message))
    .finally(() => {
      timer = setTimeout(loop, config.collectIntervalMs)
    })
}

export function startCollector() {
  if (running) return
  running = true
  collectOnce()
    .catch((e) => console.error('[collector] tick failed:', e && e.message))
    .finally(() => {
      timer = setTimeout(loop, config.collectIntervalMs)
    })
}

export function stopCollector() {
  running = false
  if (timer) clearTimeout(timer)
  timer = null
}
