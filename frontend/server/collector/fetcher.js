// 采集网络层：带超时的 JSON 拉取与 {code,msg,data} 成功判定。

import { config } from '../config.js'

export async function fetchJson(url) {
  const controller = new AbortController()
  const t = setTimeout(() => controller.abort(), config.fetchTimeoutMs)
  try {
    const res = await fetch(url, { signal: controller.signal })
    if (!res.ok) return { code: res.status, msg: `HTTP ${res.status}`, data: null }
    return await res.json()
  } catch (e) {
    return { code: -1, msg: e && e.message ? e.message : String(e), data: null }
  } finally {
    clearTimeout(t)
  }
}

export const isOk = (body) =>
  body && typeof body === 'object' && body.code === 0 && body.data != null
