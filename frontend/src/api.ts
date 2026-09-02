import type {
  Distributions,
  HistoryResponse,
  IpPage,
  Overview,
  SiteSummary,
  Stats,
} from './types'

export interface ApiEnvelope<T> {
  code: number
  msg: string
  data: T
}

export class ApiError extends Error {}

async function request<T>(
  path: string,
  params?: Record<string, string | number>,
  method: 'GET' | 'POST' = 'GET',
): Promise<T> {
  const qs =
    params && Object.keys(params).length
      ? '?' +
        new URLSearchParams(
          Object.entries(params)
            .filter(([, v]) => v !== '' && v !== undefined && v !== null)
            .map(([k, v]) => [k, String(v)]),
        ).toString()
      : ''

  let res: Response
  try {
    res = await fetch(`/api${path}${qs}`, { method })
  } catch {
    throw new ApiError('无法连接后端服务')
  }

  let body: ApiEnvelope<T>
  try {
    body = (await res.json()) as ApiEnvelope<T>
  } catch {
    throw new ApiError('后端响应解析失败')
  }
  if (!body || body.code !== 0) {
    throw new ApiError(body?.msg || '接口返回错误')
  }
  return body.data
}

export const api = {
  overview: () => request<Overview>('/overview'),
  distributions: () => request<Distributions>('/distributions'),
  stats: () => request<Stats>('/stats'),
  sites: () => request<SiteSummary[]>('/sites'),
  releaseAll: (site: string) =>
    request<number>('/sites/' + encodeURIComponent(site) + '/release-all', undefined, 'POST'),
  ips: (p: {
    protocol?: string
    status?: string
    site?: string
    page?: number
    size?: number
  }) => request<IpPage>('/ips', p),
  history: (range: string) => request<HistoryResponse>('/history', { range }),
}
