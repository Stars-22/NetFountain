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
