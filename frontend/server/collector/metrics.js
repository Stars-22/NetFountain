// 采集落库行构造：metrics 聚合行（level1 / 站点 / global 汇总）与全量 IP 快照行。
// 纯函数：数据由调用方传入，输出与 db.js 两张表的列一一对应。

import { int, num, protoMap, latencyStats } from '../util.js'

export function level1MetricsRow(now, s) {
  if (!s) return null
  const counts = protoMap(s.counts)
  const errors = s.errors || {}
  return {
    ts: now, site: 'level1',
    pool_capacity: int(s.pool_size),
    available_count: int(s.pool_size),
    leased_count: 0,
    avg_latency: null, min_latency: null, max_latency: null,
    by_proto: JSON.stringify(counts),
    total_pulled: int(s.total_pulled),
    total_entered: int(s.total_entered),
    total_duplicates: int(s.total_duplicates),
    pull_failures: int(errors.pull_failures),
    test_failures: int(errors.test_failures),
    sync_failures: 0,
    revalidate_failures: 0,
    ttl_sweep_failures: int(errors.ttl_sweep_failures),
    empty_acquires: 0,
    drops: int(s.drops),
  }
}

export function siteMetricsRow(now, name, st, ips) {
  const ps = st.pool_stats || {}
  const byProto = protoMap(ps.by_proto)
  const errors = st.errors || {}
  const lat = latencyStats(ips)
  return {
    ts: now, site: name,
    pool_capacity: int(ps.total),
    available_count: int(ps.free_total),
    leased_count: int(ps.leased_total),
    avg_latency: lat.avg, min_latency: lat.min, max_latency: lat.max,
    by_proto: JSON.stringify(byProto),
    total_pulled: int(st.total_pulled),
    total_entered: int(st.total_entered),
    total_duplicates: 0,
    pull_failures: 0,
    test_failures: int(errors.test_failures),
    sync_failures: int(errors.sync_failures),
    revalidate_failures: int(errors.revalidate_failures),
    ttl_sweep_failures: int(errors.ttl_sweep_failures),
    empty_acquires: int(errors.empty_acquires),
    drops: int(st.drops),
  }
}

export function globalMetricsRow(now, l1Row, siteRows) {
  const poolCapacity = l1Row ? l1Row.pool_capacity : null
  let available = 0
  let leased = 0
  let latSum = 0
  let latN = 0
  let minLat = null
  let maxLat = null
  const byProto = { http: 0, https: 0, socks4: 0, socks5: 0 }
  const err = {
    pull_failures: 0, test_failures: 0, sync_failures: 0,
    revalidate_failures: 0, ttl_sweep_failures: 0, empty_acquires: 0,
  }
  let drops = 0

  for (const r of siteRows) {
    available += r.available_count || 0
    leased += r.leased_count || 0
    if (r.avg_latency != null) {
      const p = protoMap(JSON.parse(r.by_proto))
      const total = p.http + p.https + p.socks4 + p.socks5
      latSum += r.avg_latency * (total || 1)
      latN += total || 1
    }
    if (r.min_latency != null && (minLat === null || r.min_latency < minLat)) minLat = r.min_latency
    if (r.max_latency != null && (maxLat === null || r.max_latency > maxLat)) maxLat = r.max_latency
    const p = protoMap(JSON.parse(r.by_proto))
    byProto.http += p.http; byProto.https += p.https
    byProto.socks4 += p.socks4; byProto.socks5 += p.socks5
    err.pull_failures += r.pull_failures || 0
    err.test_failures += r.test_failures || 0
    err.sync_failures += r.sync_failures || 0
    err.revalidate_failures += r.revalidate_failures || 0
    err.ttl_sweep_failures += r.ttl_sweep_failures || 0
    err.empty_acquires += r.empty_acquires || 0
    drops += r.drops || 0
  }

  if (l1Row) {
    err.pull_failures += l1Row.pull_failures || 0
    err.test_failures += l1Row.test_failures || 0
    err.ttl_sweep_failures += l1Row.ttl_sweep_failures || 0
    drops += l1Row.drops || 0
  }

  return {
    ts: now, site: 'global',
    pool_capacity: poolCapacity,
    available_count: available,
    leased_count: leased,
    avg_latency: latN > 0 ? latSum / latN : null,
    min_latency: minLat, max_latency: maxLat,
    by_proto: JSON.stringify(byProto),
    total_pulled: l1Row ? l1Row.total_pulled : 0,
    total_entered: l1Row ? l1Row.total_entered : 0,
    total_duplicates: l1Row ? l1Row.total_duplicates : 0,
    pull_failures: err.pull_failures,
    test_failures: err.test_failures,
    sync_failures: err.sync_failures,
    revalidate_failures: err.revalidate_failures,
    ttl_sweep_failures: err.ttl_sweep_failures,
    empty_acquires: err.empty_acquires,
    drops,
  }
}

export function buildSnapshots(now, state) {
  const rows = []
  const push = (site, ips, status) => {
    if (!Array.isArray(ips)) return
    for (const ip of ips) {
      rows.push({
        ts: now, site,
        proxy_url: ip.proxy_url || '',
        protocol: ip.protocol || null,
        region: ip.region || null,
        latency_ms: num(ip.latency_ms),
        status,
        ttl: num(ip.ttl),
        created_at: num(ip.created_at),
      })
    }
  }
  push('level1', state.level1Ips, 'free')
  for (const [name, s] of Object.entries(state.sites)) {
    push(name, s.ips, 'free')
  }
  return rows
}
