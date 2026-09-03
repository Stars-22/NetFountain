"""Phase 2 测试 conftest：网络打桩（aioresponses）、mock 供应商配置、混合协议池、运行中的应用。

提供：
- aioresponses × aiohttp 3.14 兼容 shim
- 供应商桩 URL/配置/请求 URL 构造
- 混合协议 Level1Pool fixture
- 单事件循环内运行 FastAPI 应用的 async 上下文管理器（running_app）
- 本地 mock 供应商 HTTP 服务（mock_server，集成测试用）
"""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from unittest import mock

import aiohttp
import aioresponses
import aioresponses.core
import pytest
from aiohttp.http_writer import StreamWriter

from app.config import ProviderConfig
from app.pool import Level1Pool
from ip_pool_common.models import Protocol, ProviderIp

# ---------------------------------------------------------------------------
# aioresponses 0.7.9 与 aiohttp 3.14 兼容 shim（同 common/tests/conftest.py）
# ---------------------------------------------------------------------------


class _CompatClientResponse(aioresponses.core.ClientResponse):
    def __init__(self, method, url, **kwargs):  # noqa: ANN001
        if "stream_writer" not in kwargs:
            kwargs["stream_writer"] = StreamWriter(
                mock.MagicMock(), kwargs.get("loop")
            )
        super().__init__(method, url, **kwargs)


aioresponses.core.ClientResponse = _CompatClientResponse


# ---------------------------------------------------------------------------
# 供应商桩：URL / 配置 / 请求 URL 构造
# ---------------------------------------------------------------------------
STUB_API_URL = "http://mock.provider.example/api/proxies"
STUB_API_KEY = "test-key"


@pytest.fixture
def provider_cfg():
    """指向打桩 URL 的供应商配置。"""
    return ProviderConfig(
        type="default_http",
        api_url=STUB_API_URL,
        api_key=STUB_API_KEY,
        pull_count=10,
        pull_interval=1.0,
        pull_timeout=5.0,
        supports_ttl=False,
    )


@pytest.fixture
def provider_request_url():
    """构造供应商实际请求 URL（含 query 参数），供 aioresponses 打桩精确匹配。"""

    def _make(count: int = 10, api_key: str = STUB_API_KEY, api_url: str = STUB_API_URL) -> str:
        qs = f"count={count}"
        if api_key:
            qs += f"&api_key={api_key}"
        return f"{api_url}?{qs}"

    return _make


@pytest.fixture
def settings(provider_cfg):
    """带打桩供应商配置的 Level1Settings。"""
    from app.config import Level1Settings

    return Level1Settings(provider=provider_cfg)


# ---------------------------------------------------------------------------
# 91HTTP 供应商桩：URL / 配置 / 请求 URL 构造
# ---------------------------------------------------------------------------
HTTP91_API_URL = "http://api.91http.com/v1/get-ip"
HTTP91_TRADE_NO = "A161832894358"
HTTP91_SECRET = "TkZQYrTD9iI1FfDE"


@pytest.fixture
def http91_cfg():
    """指向 91HTTP 打桩 URL 的供应商配置。"""
    return ProviderConfig(
        type="http91",
        api_url=HTTP91_API_URL,
        api_key=HTTP91_SECRET,
        trade_no=HTTP91_TRADE_NO,
        protocol=1,
        pull_count=10,
        pull_interval=1.0,
        pull_timeout=5.0,
        supports_ttl=True,
    )


@pytest.fixture
def http91_request_url():
    """构造 91HTTP 实际请求 URL（含 query 参数），供 aioresponses 打桩精确匹配。"""

    def _make(
        count: int = 10,
        trade_no: str = HTTP91_TRADE_NO,
        secret: str = HTTP91_SECRET,
        protocol: int = 1,
        api_url: str = HTTP91_API_URL,
    ) -> str:
        qs = (
            f"trade_no={trade_no}&secret={secret}&num={count}"
            f"&format=json&time=1&protocol={protocol}"
        )
        return f"{api_url}?{qs}"

    return _make


# ---------------------------------------------------------------------------
# freeproxy 供应商桩：URL / 配置 / 请求 URL 构造
# ---------------------------------------------------------------------------
FREEPROXY_API_URL = "http://www.zdopen.com/FreeProxy/Get/"
FREEPROXY_APP_ID = "202608211839203977"
FREEPROXY_AKEY = "9fdd09efee3b49d8"


@pytest.fixture
def freeproxy_cfg():
    """指向 freeproxy 打桩 URL 的供应商配置。"""
    return ProviderConfig(
        type="freeproxy",
        api_url=FREEPROXY_API_URL,
        api_key=FREEPROXY_AKEY,
        trade_no=FREEPROXY_APP_ID,
        dalu=1,
        protocol_type=0,
        pull_count=10,
        pull_interval=1.0,
        pull_timeout=5.0,
        supports_ttl=False,
    )


@pytest.fixture
def freeproxy_request_url():
    """构造 freeproxy 实际请求 URL（含 query 参数），供 aioresponses 打桩精确匹配。"""

    def _make(
        count: int = 10,
        app_id: str = FREEPROXY_APP_ID,
        akey: str = FREEPROXY_AKEY,
        dalu: int = 1,
        protocol_type: int = 0,
        api_url: str = FREEPROXY_API_URL,
    ) -> str:
        qs = (
            f"app_id={app_id}&akey={akey}&count={min(count, 100)}"
            f"&dalu={dalu}&return_type=3"
        )
        if protocol_type > 0:
            qs += f"&protocol_type={protocol_type}"
        return f"{api_url}?{qs}"

    return _make


# ---------------------------------------------------------------------------
# 网络桩
# ---------------------------------------------------------------------------


@pytest.fixture
def aio_mock():
    """aioresponses 上下文：测试内通过 m.get(url, ...) 打桩。"""
    with aioresponses.aioresponses() as m:
        yield m


@pytest.fixture
async def mock_session(aio_mock):
    """基于 aioresponses 的 aiohttp 桩会话。"""
    async with aiohttp.ClientSession() as session:
        yield session, aio_mock


# ---------------------------------------------------------------------------
# 池与数据构造
# ---------------------------------------------------------------------------


@pytest.fixture
def make_ip():
    def _make(idx: int, protocol=Protocol.HTTP, region=None, ttl=None) -> ProviderIp:
        return ProviderIp(
            ip=f"10.0.0.{idx}",
            port=8000 + idx,
            protocol=protocol,
            region=region,
            ttl=ttl,
        )

    return _make


@pytest.fixture
def pool():
    return Level1Pool(max_size=100)


@pytest.fixture
async def mixed_pool(make_ip):
    """预置多协议记录的池：HTTP/HTTPS/SOCKS4/SOCKS5/HTTP。"""
    p = Level1Pool(max_size=100)
    now = 1000.0
    specs = [
        (1, Protocol.HTTP),
        (2, Protocol.HTTPS),
        (3, Protocol.SOCKS4),
        (4, Protocol.SOCKS5),
        (5, Protocol.HTTP),
    ]
    for idx, proto in specs:
        await p.add(make_ip(idx, protocol=proto, region="CN", ttl=120), now)
    return p


# ---------------------------------------------------------------------------
# 运行中的应用（单事件循环，供 routes/集成测试）
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _run_app(app):
    """进入 lifespan 并在同一事件循环内提供 HTTP 客户端。"""
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            yield client


@pytest.fixture
def running_app():
    """async 上下文管理器工厂：``async with running_app(app) as client``。"""
    return _run_app


# ---------------------------------------------------------------------------
# 本地 mock 供应商 HTTP 服务（集成测试）
# ---------------------------------------------------------------------------


@pytest.fixture
async def mock_server():
    """在同一事件循环内启动 mock_provider 服务，yield 其 base URL。"""
    import uvicorn

    import mock_provider

    mock_provider.state.reset()
    config = uvicorn.Config(
        mock_provider.app, host="127.0.0.1", port=0, log_level="error"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started, "mock provider server failed to start"
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=3)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
