// 分布聚合：/api/distributions（level1 仅 TTL；二级池 TTL + 延迟）。
// 分桶定义见 buckets.js（表驱动）。

import { getState } from '../collector/index.js'
import { TTL_BUCKETS, LATENCY_BUCKETS } from '../buckets.js'
import { num } from '../util.js'

export function ttlDistribution(ips, now) {
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

export function latencyDistribution(ips) {
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

export function buildDistributions() {
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
