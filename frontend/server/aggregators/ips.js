// IP 列表聚合：全量二级池 IP 展平 + /api/ips 过滤分页。

import { getState } from '../collector/index.js'
import { int, num } from '../util.js'

export function allLevel2Ips() {
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

export function listIps(query) {
  const protocol = String(query.protocol || '')
  const status = String(query.status || '')
  const site = String(query.site || '')
  const page = Math.max(1, int(query.page) || 1)
  const size = Math.min(200, Math.max(1, int(query.size) || 20))

  let items = allLevel2Ips()
  if (protocol) items = items.filter((i) => i.protocol === protocol)
  if (status === 'free') items = items.filter((i) => !i.leased)
  if (status === 'leased') items = items.filter((i) => i.leased)
  if (site) items = items.filter((i) => i.site === site)

  const total = items.length
  const start = (page - 1) * size
  return { total, page, size, items: items.slice(start, start + size) }
}
