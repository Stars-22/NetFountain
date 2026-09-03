export interface ProtoCounts {
  http: number
  https: number
  socks4: number
  socks5: number
}

export interface Level1Strip {
  ip_count: number | null
  uptime: number | null
  total_pulled: number
  pass_rate: number | null
  duplicate_rate: number | null
  by_proto: ProtoCounts
  errors: Record<string, number>
  drops: number
  errors_total: number
  api_call_count: number | null
  avg_remaining: number | null
  stale: boolean
}

export interface SiteStrip {
  name: string
  reachable: boolean
  stale: boolean
  target_url: string | null
  base_url: string | null
  ip_count: number
  free: number
  leased: number
  uptime: number | null
  total_pulled: number
  pass_rate: number | null
  avg_latency: number | null
  by_proto: ProtoCounts
  errors: Record<string, number>
  drops: number
  errors_total: number
  api_call_count: number | null
  avg_remaining: number | null
}

export interface Overview {
  updated_at: number
  level1: Level1Strip | null
  sites: SiteStrip[]
}

export interface SiteSummary {
  name: string
  target_url: string | null
  base_url: string | null
  reachable: boolean
  stale: boolean
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

export interface PoolDistributions {
  ttl: Distribution[]
  latency?: Distribution[]
}

export interface Distributions {
  updated_at: number
  pools: Record<string, PoolDistributions>
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
  leased_count: number | null
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
    uptime: number | null
    started_at: string | null
    total_calls: number
    calls_by_ip: Record<string, number>
    calls_by_site: Record<string, number>
    errors: Record<string, number>
  } | null
  updated_at: number
}
