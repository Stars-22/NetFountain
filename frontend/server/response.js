// 统一响应封装：{code, msg, data} 契约（与 Python 服务一致）。

export const ok = (data) => ({ code: 0, msg: 'ok', data })
export const err = (code, msg) => ({ code, msg, data: null })
