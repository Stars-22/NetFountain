// 共享工具：数值规整、协议计数、延迟统计。

export function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

export function int(v) {
  const n = num(v)
  return n === null ? null : Math.round(n)
}

export function protoMap(byProto) {
  const out = { http: 0, https: 0, socks4: 0, socks5: 0 }
  if (byProto && typeof byProto === 'object') {
    for (const k of Object.keys(out)) out[k] = int(byProto[k]) || 0
  }
  return out
}

export function latencyStats(ips) {
  if (!Array.isArray(ips) || ips.length === 0) {
    return { avg: null, min: null, max: null, count: 0 }
  }
  let sum = 0
  let min = Infinity
  let max = -Infinity
  let n = 0
  for (const ip of ips) {
    const l = num(ip.latency_ms)
    if (l === null) continue
    sum += l
    if (l < min) min = l
    if (l > max) max = l
    n += 1
  }
  if (n === 0) return { avg: null, min: null, max: null, count: 0 }
  return { avg: sum / n, min, max, count: n }
}

// 平均剩余时间：有限 TTL 记录的 max(0, created_at + ttl - now) 平均值；
// ttl 为 null/undefined（永久）的记录不参与平均。无有效记录返回 null。
// 注意：num(null) 会得到 0（Number(null)===0），必须先显式判空再走 num。
export function avgRemainingSeconds(ips, now) {
  if (!Array.isArray(ips) || ips.length === 0) return null
  const t = now == null ? Date.now() / 1000 : now
  let sum = 0
  let n = 0
  for (const ip of ips) {
    if (ip.ttl == null) continue
    const ttl = num(ip.ttl)
    if (ttl === null) continue
    const created = num(ip.created_at) || 0
    const remaining = created + ttl - t
    sum += remaining > 0 ? remaining : 0
    n += 1
  }
  if (n === 0) return null
  return sum / n
}
