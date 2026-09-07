// 统计聚合：/api/stats（level1 汇总 + 站点明细 + 代理层自身统计）。

import { getState } from '../collector/index.js'
import { buildSiteSummaries } from './sites.js'
import { int, num } from '../util.js'

export function buildStats() {
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
