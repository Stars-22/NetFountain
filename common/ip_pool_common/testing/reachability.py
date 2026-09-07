"""代理可达性测试：纯 python_socks 握手，零出口流量。

核心语义（硬性约束）：``proxy_reachability_test`` 只验证「能否与代理建立代理协议
会话」，**不做任何出口验证**。站点连通测试（``site_test``）是唯一的出口验证，
仅二级池入池时使用。
"""
from __future__ import annotations

import asyncio
import time

from python_socks import (
    ProxyError as PySocksProxyError,
    parse_proxy_url,
)
from python_socks.async_.asyncio.v2 import Proxy

from ..errors import classify_test_error

#: 可达性探测的占位目标。仅在真实环境下由代理尝试解析/连接（CONNECT 目标），
#: `.invalid` 顶级域保证解析必然失败，代理会快速返回 502/拒绝应答，从而能
#: 仅凭「收到合法代理应答」判定代理本身可达，而无需依赖任何真实出口。
PROBE_HOST = "placeholder.invalid"
PROBE_PORT = 443
PROBE_TARGET = f"http://{PROBE_HOST}:{PROBE_PORT}/"


def _is_legal_proxy_reply(error: BaseException) -> bool:
    """判定代理是否返回了合法的代理协议应答（非连接层故障）。

    python-socks 对非 200 的 CONNECT / 非成功 SOCKS 应答会抛出 ``ProxyError``，
    其 ``error_code`` 为 HTTP 状态码或 SOCKS 应答码。HTTP 状态码落在
    [400, 599] 区间，属于合法的 HTTP 代理应答（含 407 鉴权），判为可达。
    """
    code = getattr(error, "error_code", None)
    return code is not None and 400 <= code < 600


async def proxy_reachability_test(
    proxy_url: str,
    timeout: float = 3.0,
    session: object | None = None,
) -> tuple[bool, float]:
    """只验证能否与代理建立代理协议会话，不做任何出口验证。

    通过 python_socks 直接完成「纯握手」（不发内层请求，零出口流量）：
    - http/https：向代理发送 ``CONNECT`` 并读取应答。收到 2xx（隧道建立）
      即判可达；收到 4xx/5xx（合法代理应答，含 407 鉴权）也判可达；
      连接拒绝 / 超时 / 无应答判不可达。
    - socks4/socks5：完成 greeting + CONNECT 握手；握手成功（REP=0x00）
      即判可达；拒绝 / 超时 / 连接失败判不可达。

    关键点：只做握手、**绝不发送内层请求**（不测出口）。这使 lazy-CONNECT
    类代理（对任何 CONNECT 立即回 200、随后自行解析上游目标）也能被正确
    判定为可达——此类代理对不可解析目标会关闭隧道，若像旧实现那样在握手后
    再发内层请求，会产生对可用代理的误判。

    返回 ``(ok, latency_ms)``。``session`` 参数仅保留以兼容旧签名，本实现
    不再使用（握手直接经 python_socks 完成）。需要失败原因时请使用
    ``proxy_reachability_test_detailed``。
    """
    ok, latency, _ = await _proxy_reachability_impl(proxy_url, timeout)
    return ok, latency


async def proxy_reachability_test_detailed(
    proxy_url: str,
    timeout: float = 3.0,
    session: object | None = None,
) -> tuple[bool, float, str | None]:
    """同 :func:`proxy_reachability_test`，额外返回失败原因键（成功为 None）。

    供上层（如二级池批次汇总日志）区分失败原因（timeout/connect/proxy_reject/...）。
    """
    return await _proxy_reachability_impl(proxy_url, timeout)


async def _proxy_reachability_impl(
    proxy_url: str, timeout: float = 3.0
) -> tuple[bool, float, str | None]:
    """``proxy_reachability_test`` 实现：额外返回失败原因键（成功为 None）。

    原因键见 :func:`ip_pool_common.errors.classify_test_error`，
    另含 ``proxy_reject``（代理协议拒绝）。
    """
    start = time.perf_counter()
    try:
        proxy_type, host, port, username, password = parse_proxy_url(proxy_url)
    except (ValueError, TypeError):
        return False, 0.0, "invalid_proxy"
    proxy = Proxy(
        proxy_type=proxy_type,
        host=host,
        port=port,
        username=username,
        password=password,
        rdns=True,
    )
    stream = None
    reason: str | None = None
    try:
        try:
            stream = await proxy.connect(
                dest_host=PROBE_HOST,
                dest_port=PROBE_PORT,
                timeout=timeout,
            )
            ok = True
        except PySocksProxyError as exc:
            ok = _is_legal_proxy_reply(exc)
            if not ok:
                reason = "proxy_reject"
        except (
            asyncio.TimeoutError,
            TimeoutError,
            OSError,
        ) as exc:
            ok = False
            reason = classify_test_error(exc)
    except asyncio.CancelledError:
        raise
    finally:
        if stream is not None:
            try:
                await stream.close()
            except Exception:
                pass
    return ok, (time.perf_counter() - start) * 1000.0, reason
