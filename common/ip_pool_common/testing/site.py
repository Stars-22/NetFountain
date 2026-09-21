"""站点连通测试：经代理真实访问目标站点，验证出口可达。

唯一出口验证入口，仅二级池入池时使用；一级池复验只测代理可达性
（见 ``reachability.py``）。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping

import aiohttp
from aiohttp_socks import ProxyConnector, ProxyError
from python_socks._protocols.errors import ReplyError

from ..errors import classify_test_error


def _client_timeout(timeout: float) -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=timeout)


async def site_test(
    proxy_url: str,
    target_url: str,
    timeout: float = 3.0,
    session: aiohttp.ClientSession | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[bool, float]:
    """经代理真实访问目标站点，验证出口可达。

    收到任意 <500 的 HTTP 响应即判 ok=True（5xx/连接错误/超时判 ok=False），
    latency_ms 为完成请求耗时。二级池入池测试用（<2000ms 才入池）。

    ``headers`` 为随目标请求发送的附加请求头（如 ``User-Agent``），
    缺省 None 时使用 aiohttp 默认请求头。

    需要失败原因时请使用 ``site_test_detailed``。
    """
    ok, latency, _ = await _site_test_impl(
        proxy_url, target_url, timeout, session, headers
    )
    return ok, latency


async def site_test_detailed(
    proxy_url: str,
    target_url: str,
    timeout: float = 3.0,
    session: aiohttp.ClientSession | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[bool, float, str | None]:
    """同 :func:`site_test`，额外返回失败原因键（成功为 None）。

    供上层（如二级池批次汇总日志）区分失败原因（timeout/connect/http_5xx/...）。
    """
    return await _site_test_impl(proxy_url, target_url, timeout, session, headers)


async def _site_test_impl(
    proxy_url: str,
    target_url: str,
    timeout: float = 3.0,
    session: aiohttp.ClientSession | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[bool, float, str | None]:
    """``site_test`` 实现：额外返回失败原因键（成功为 None）。

    原因键含 :func:`ip_pool_common.errors.classify_test_error` 的归类，
    另加 ``http_5xx``（目标站 5xx）。
    """
    start = time.perf_counter()
    connector: ProxyConnector | None = None
    own_session = False
    reason: str | None = None
    if session is None:
        try:
            connector = ProxyConnector.from_url(proxy_url)
        except ValueError:
            return False, 0.0, "invalid_proxy"
        session = aiohttp.ClientSession(connector=connector)
        own_session = True
    try:
        try:
            async with session.get(
                target_url, timeout=_client_timeout(timeout), headers=headers
            ) as resp:
                await resp.read()
                ok = resp.status < 500
                if not ok:
                    reason = "http_5xx"
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            TimeoutError,
            OSError,
            ValueError,
            ProxyError,
            ReplyError,
        ) as exc:
            ok = False
            reason = classify_test_error(exc)
    finally:
        if own_session:
            await session.close()
        if connector is not None:
            await connector.close()
    return ok, (time.perf_counter() - start) * 1000.0, reason
