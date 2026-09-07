// 分桶与时间范围常量表：分布图与 /api/history 降采样共用。
// 表驱动（开闭）：调整分档/新增时间范围只改这里。

// ---- 时间范围 -> 降采样桶宽（秒） ----
export const RANGES = {
  '1h': 60,
  '6h': 300,
  '24h': 600,
  '7d': 3600,
}

// TTL 剩余分布分桶（剩余 = created_at + ttl - now；已过期归入 ≤1min）
export const TTL_BUCKETS = [
  { name: '≤1min', min: 0, max: 60 },
  { name: '1~3min', min: 60, max: 180 },
  { name: '3~5min', min: 180, max: 300 },
  { name: '5~10min', min: 300, max: 600 },
  { name: '10~30min', min: 600, max: 1800 },
  { name: '30min~2h', min: 1800, max: 7200 },
  { name: '2h~6h', min: 7200, max: 21600 },
  { name: '6h~12h', min: 21600, max: 43200 },
  { name: '12h~24h', min: 43200, max: 86400 },
  { name: '≥24h', min: 86400, max: Infinity },
]

export const LATENCY_BUCKETS = [
  { name: '<200ms', min: 0, max: 200 },
  { name: '200-500ms', min: 200, max: 500 },
  { name: '500-1000ms', min: 500, max: 1000 },
  { name: '1000-2000ms', min: 1000, max: 2000 },
  { name: '2000-3000ms', min: 2000, max: 3000 },
  { name: '≥3000ms', min: 3000, max: Infinity },
]
