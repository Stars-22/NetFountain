export interface ProtoCounts {
  http: number
  https: number
  socks4: number
  socks5: number
}

export interface Overview {
  pool_capacity: number | null
  available_count: number
  leased_count: number
  avg_latency: number | null
  by_proto: ProtoCounts
  pull_rate: number | null
  pass_rate: number | null
  duplicate_rate: number | null
  errors_total: number
  errors: Record<string, number>
  proxy_errors: number
  updated_at: number
}

export interface SiteSummary {
  name: string
  target_url: string | null
  base_url: string | null
  reachable: boolean
  total: number
  leased_total: number
  free_total: number
  by_proto: ProtoCounts
  avg_latency: number | null
  pass_rate: number | null
  errors: Record<string, number>
  drops: number
}

export interface Distribution {
  name: string
  value: number
}

export interface Distributions {
  latency: Distribution[]
  ttl: Distribution[]
  updated_at: number
}

export interface IpItem {
  site: string
  proxy_url: string
  protocol: string
  region: string | null
  latency_ms: number | null
  leased: boolean
  ttl: number | null
  created_at: number | null
}

export interface IpPage {
  total: number
  page: number
  size: number
  items: IpItem[]
}

export interface HistoryPoint {
  ts: number
  pool_capacity: number | null
  available_count: number | null
  avg_latency: number | null
  pull_rate: number
  pass_rate: number | null
  duplicate_rate: number | null
  errors: Record<string, number>
}

export interface HistoryResponse {
  range: string
  bucketSec: number
  series: Record<string, HistoryPoint[]>
}

export interface Stats {
  level1: {
    pool_size: number | null
    total_pulled: number | null
    total_entered: number | null
    total_duplicates: number | null
    errors: Record<string, number>
    drops: number
  } | null
  sites: SiteSummary[]
  proxy: {
    total_calls: number
    calls_by_site: Record<string, number>
    errors: Record<string, number>
  } | null
  updated_at: number
}
