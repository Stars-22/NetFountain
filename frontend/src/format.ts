export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined) return '-'
  return String(Math.round(v))
}

export function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '-'
  return (v * 100).toFixed(1) + '%'
}

export function fmtMs(v: number | null | undefined): string {
  if (v === null || v === undefined) return '-'
  return v.toFixed(0) + ' ms'
}

export function fmtRate(v: number | null | undefined): string {
  if (v === null || v === undefined) return '-'
  return v.toFixed(2) + ' IP/s'
}

export function fmtTime(ts: number, range?: string): string {
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  const hhmm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  if (range === '7d' || range === '24h') {
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hhmm}`
  }
  return hhmm
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '-'
  const s = Math.floor(seconds)
  if (s <= 0) return '-'
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (d > 0) return `${d}d${h}h`
  if (h > 0) return `${h}h${m}m`
  if (m > 0) return `${m}m${sec}s`
  return `${sec}s`
}

export function latencyType(ms: number | null): 'success' | 'warning' | 'danger' | 'info' {
  if (ms === null || ms === undefined) return 'info'
  if (ms < 500) return 'success'
  if (ms < 2000) return 'warning'
  return 'danger'
}

// 剩余时间 = 现在到结束（created_at + ttl - now），而非拉取时的原始 ttl
export function remainingSeconds(
  rec: { ttl: number | null; created_at: number | null },
  now?: number,
): number | null {
  if (rec.ttl === null || rec.ttl === undefined) return null
  const t = now ?? Date.now() / 1000
  const remaining = (rec.created_at || 0) + rec.ttl - t
  return remaining > 0 ? remaining : 0
}

export function fmtRemaining(
  rec: { ttl: number | null; created_at: number | null },
): string {
  const r = remainingSeconds(rec)
  if (r === null) return '永久'
  if (r <= 0) return '已过期'
  return fmtDuration(r)
}

// 多序列折线图调色板（按系列索引取色）
export const PALETTE = [
  '#409EFF', '#67C23A', '#E6A23C', '#F56C6C', '#9C27B0',
  '#00BCD4', '#FF7F50', '#8BC34A', '#FFB6C1', '#7E57C2',
]

export const ERROR_LABELS: Record<string, string> = {
  pull_failures: '拉取失败',
  test_failures: '测试失败',
  sync_failures: '同步失败',
  revalidate_failures: '复验失败',
  ttl_sweep_failures: 'TTL清扫失败',
  empty_acquires: '空池租赁',
  drops: '丢弃批次',
}
