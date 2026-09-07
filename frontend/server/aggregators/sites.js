// 站点明细聚合：/api/sites 数据源（含站点不可达时的零值占位）。

import { getState } from '../collector/index.js'
import { int, num, protoMap, latencyStats } from '../util.js'

export function buildSiteSummaries() {
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
