// 总览聚合：/api/overview（一级池 1 条 + 每个二级池 1 条，字段为各页所需超集）。

import { getState } from '../collector/index.js'
import { int, num, protoMap, latencyStats, avgRemainingSeconds } from '../util.js'

// 池级错误总数：errors 各项求和 + drops
export function poolErrorsTotal(errors, drops) {
  let total = 0
  if (errors && typeof errors === 'object') {
    for (const v of Object.values(errors)) total += int(v) || 0
  }
  return total + (int(drops) || 0)
}

export function buildOverview() {
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

