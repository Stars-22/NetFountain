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

export function fmtDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '-'
  const s = Math.floor(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
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
