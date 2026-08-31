"""代理测试原语：可达性测试、站点连通测试、批量并发测试。

核心语义（硬性约束）：`proxy_reachability_test` 只验证「能否与代理建立代理协议
会话」，**不做任何出口验证**。站点连通测试（`site_test`）是唯一的出口验证，
仅二级池入池时使用。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import aiohttp
from aiohttp_socks import ProxyConnector, ProxyError
from python_socks import (
    ProxyConnectionError,
    ProxyError as PySocksProxyError,
    ProxyTimeoutError,
    ProxyType,
    parse_proxy_url,
)
from python_socks.async_.asyncio.v2 import Proxy
from python_socks._protocols.errors import ReplyError

#: 可达性探测的占位目标。仅在真实环境下由代理尝试解析/连接（CONNECT 目标），
#: `.invalid` 顶级域保证解析必然失败，代理会快速返回 502/拒绝应答，从而能
#: 仅凭「收到合法代理应答」判定代理本身可达，而无需依赖任何真实出口。
PROBE_HOST = "placeholder.invalid"
PROBE_PORT = 443
PROBE_TARGET = f"http://{PROBE_HOST}:{PROBE_PORT}/"


def _client_timeout(timeout: float) -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=timeout)


def _is_legal_proxy_reply(error: BaseException) -> bool:
    """判定代理是否返回了合法的代理协议应答（非连接层故障）。

    python-socks 对非 200 的 CONNECT / 非成功 SOCKS 应答会抛出 ``ProxyError``，
    其 ``error_code`` 为 HTTP 状态码或 SOCKS 应答码。HTTP 状态码落在
    [400, 599] 区间，属于合法的 HTTP 代理应答（含 407 鉴权），判为可达。
    """
    code = getattr(error, "error_code", None)
    return code is not None and 400 <= code < 600


def classify_test_error(error: BaseException) -> str:
    """将测试异常归类为简短原因键，供批次汇总日志展示。

    判定顺序依赖异常继承层级（已验证）：
    - ``ProxyTimeoutError`` 是 ``TimeoutError``/``OSError`` 子类；
    - ``ProxyConnectionError`` 是 ``OSError`` 子类；
    - ``aiohttp.ClientConnectorError`` 是 ``OSError`` 子类；
    - ``aiohttp.ServerTimeoutError`` 是 ``TimeoutError`` 子类。
    因此先判超时、再判连接，最后才是代理协议/业务类异常。
    """
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(error, (OSError, ConnectionError)):
        return "connect"
    if isinstance(error, (ProxyError, PySocksProxyError, ReplyError)):
        return "proxy_reject"
    if isinstance(error, ValueError):
        return "invalid_proxy"
    if isinstance(error, aiohttp.ClientError):
        return "client_error"
    return "exception"


async def proxy_reachability_test(
    proxy_url: str,
    timeout: float = 3.0,
    session: aiohttp.ClientSession | None = None,
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


async def _proxy_reachability_impl(
    proxy_url: str, timeout: float = 3.0
) -> tuple[bool, float, str | None]:
    """``proxy_reachability_test`` 实现：额外返回失败原因键（成功为 None）。

    原因键见 :func:`classify_test_error`，另含 ``proxy_reject``（代理协议拒绝）。
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
            ProxyConnectionError,
            ProxyTimeoutError,
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


async def proxy_reachability_test_detailed(
    proxy_url: str,
    timeout: float = 3.0,
    session: aiohttp.ClientSession | None = None,
) -> tuple[bool, float, str | None]:
    """同 :func:`proxy_reachability_test`，额外返回失败原因键（成功为 None）。

    供上层（如二级池批次汇总日志）区分失败原因（timeout/connect/proxy_reject/...）。
    """
    return await _proxy_reachability_impl(proxy_url, timeout)


async def site_test(
    proxy_url: str,
    target_url: str,
    timeout: float = 3.0,
    session: aiohttp.ClientSession | None = None,
) -> tuple[bool, float]:
    """经代理真实访问目标站点，验证出口可达。

    收到任意 <500 的 HTTP 响应即判 ok=True（5xx/连接错误/超时判 ok=False），
    latency_ms 为完成请求耗时。二级池入池测试用（<2000ms 才入池）。

    需要失败原因时请使用 ``site_test_detailed``。
    """
    ok, latency, _ = await _site_test_impl(proxy_url, target_url, timeout, session)
    return ok, latency


async def _site_test_impl(
    proxy_url: str,
    target_url: str,
    timeout: float = 3.0,
    session: aiohttp.ClientSession | None = None,
) -> tuple[bool, float, str | None]:
    """``site_test`` 实现：额外返回失败原因键（成功为 None）。

    原因键含 :func:`classify_test_error` 的归类，另加 ``http_5xx``（目标站 5xx）。
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
                target_url, timeout=_client_timeout(timeout)
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


async def site_test_detailed(
    proxy_url: str,
    target_url: str,
    timeout: float = 3.0,
    session: aiohttp.ClientSession | None = None,
) -> tuple[bool, float, str | None]:
    """同 :func:`site_test`，额外返回失败原因键（成功为 None）。

    供上层（如二级池批次汇总日志）区分失败原因（timeout/connect/http_5xx/...）。
    """
    return await _site_test_impl(proxy_url, target_url, timeout, session)


async def batch_test(
    items: Sequence[Any],
    test_fn: Callable[[Any], Awaitable[tuple[bool, float]]],
    concurrency: int = 20,
) -> list[Any]:
    """信号量并发批量测试：并发不超过 ``concurrency``，仅返回 ok=True 的项，保持原顺序。"""
    if concurrency < 1:
        concurrency = 1
    sem = asyncio.Semaphore(concurrency)

    async def _run(item: Any) -> tuple[Any, bool]:
        async with sem:
            try:
                ok, _ = await test_fn(item)
            except Exception:
                ok = False
        return item, ok

    results = await asyncio.gather(*(_run(item) for item in items))
    return [item for item, ok in results if ok]