// API 路由装配：注册全部 /api 端点（聚合读 + 单站点查询 + 上游转发 + 历史）。
// 开闭：新增聚合接口 = 新增聚合模块 + 在此注册一行。

import express from 'express'

import { getState } from '../collector/index.js'
import { ok, err } from '../response.js'
import { int, num } from '../util.js'
import { buildOverview } from '../aggregators/overview.js'
import { buildDistributions } from '../aggregators/distributions.js'
import { buildStats } from '../aggregators/stats.js'
import { buildSiteSummaries } from '../aggregators/sites.js'
import { listIps } from '../aggregators/ips.js'
import { registerHistoryRoute } from './history.js'
import { registerUpstreamRoutes } from './upstream.js'

export function registerRoutes(app) {
  app.use(express.json())

  app.get('/api/health', (req, res) => res.json(ok({ status: 'ok', now: Date.now() })))
  app.get('/api/overview', (req, res) => res.json(ok(buildOverview())))
  app.get('/api/distributions', (req, res) => res.json(ok(buildDistributions())))
  app.get('/api/stats', (req, res) => res.json(ok(buildStats())))
  app.get('/api/sites', (req, res) => res.json(ok(buildSiteSummaries())))

  app.get('/api/ips', (req, res) => res.json(ok(listIps(req.query))))

  app.get('/api/sites/:site/ips', (req, res) => {
    const st = getState()
    const s = st.sites[req.params.site]
    if (!s) return res.json(err(40400, `site not found: ${req.params.site}`))
    const items = (s.ips || []).map((ip) => ({
      proxy_url: ip.proxy_url || '',
      protocol: ip.protocol || '',
      region: ip.region || null,
      latency_ms: num(ip.latency_ms),
      leased: !!ip.leased,
      ttl: num(ip.ttl),
      created_at: num(ip.created_at),
    }))
    res.json(ok(items))
  })

  app.get('/api/sites/:site/status', (req, res) => {
    const st = getState()
    const s = st.sites[req.params.site]
    if (!s) return res.json(err(40400, `site not found: ${req.params.site}`))
    res.json(ok(s.status))
  })

  registerHistoryRoute(app)
  registerUpstreamRoutes(app)
}
