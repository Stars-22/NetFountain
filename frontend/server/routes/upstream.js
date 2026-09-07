// 一键释放站点全部租赁 IP：BFF 代理转发上游 release-all（前端禁止直连二级池）。
// 优先用采集缓存的 base_url（含 proxy 掉线期间），回退到代理层 health 站点表。

import { getState } from '../collector/index.js'
import { err } from '../response.js'

export function registerUpstreamRoutes(app) {
  app.post('/api/sites/:site/release-all', async (req, res) => {
    const st = getState()
    const cached = st.sites[req.params.site]
    const entry =
      cached && cached.base_url
        ? { base_url: cached.base_url }
        : (st.proxy && st.proxy.sites ? st.proxy.sites : []).find(
            (s) => s.name === req.params.site,
          )
    if (!entry || !entry.base_url) {
      return res.json(err(40400, `site not found: ${req.params.site}`))
    }
    const base = entry.base_url.replace(/\/+$/, '')
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 5000)
    try {
      const r = await fetch(`${base}/api/v1/ips/release-all`, {
        method: 'POST',
        signal: controller.signal,
      })
      let body
      try {
        body = await r.json()
      } catch {
        body = err(50200, `invalid upstream response: HTTP ${r.status}`)
      }
      res.json(body)
    } catch (e) {
      res.json(err(50200, `upstream error: ${e && e.message ? e.message : e}`))
    } finally {
      clearTimeout(timer)
    }
  })
}
