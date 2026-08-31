"""Phase 3 测试 conftest：网络打桩（aioresponses）、mock 一级池服务、混合协议租赁池、运行中的应用。

提供：
- aioresponses × aiohttp 3.14 兼容 shim
- aio_mock / mock_session（桩一级池 /ips、/ips/after/{id} 响应）
- IpRecord / Level2Record 构造工厂
- 空池 / 混合协议（租/闲）pool fixture
- 可注入延迟/结果的 tester 工厂
- 单事件循环内运行 FastAPI 应用的 async 上下文管理器（running_app）
- 本地 mock 一级池 HTTP 服务（mock_level1_server，可配置 id 空间重置模拟重启）
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

from app.pool import Level2Pool
from ip_pool_common.models import IpRecord, Level2Record, Protocol, build_proxy_url

# ---------------------------------------------------------------------------
# aioresponses 0.7.9 与 aiohttp 3.14 兼容 shim（同 common/level1 测试）
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
# 数据构造
# ---------------------------------------------------------------------------


@pytest.fixture
def make_ip():
    """构造一级池 IpRecord 工厂：``make_ip(idx, id_=..., protocol=..., ttl=...)``。"""

    def _make(
        idx: int,
        id_: int | None = None,
        protocol: Protocol = Protocol.HTTP,
        region: str | None = "CN",
        ttl: float | None = 120.0,
        created_at: float = 1000.0,
    ) -> IpRecord:
        ip = f"10.0.0.{idx}"
        port = 8000 + idx
        return IpRecord(
            id=id_ if id_ is not None else idx,
            ip=ip,
            port=port,
            protocol=protocol,
            proxy_url=build_proxy_url(ip, port, protocol),
            region=region,
            ttl=ttl,
            created_at=created_at,
        )

    return _make


@pytest.fixture
def make_l2(make_ip):
    """由 IpRecord 构造二级池 Level2Record 工厂（id 由池分配，占位 -1）。"""

    def _make(
        ip: IpRecord,
        latency: float = 10.0,
        leased: bool = False,
        created_at: float = 1000.0,
    ) -> Level2Record:
        return Level2Record(
            id=-1,
            ip=ip.ip,
            port=ip.port,
            protocol=ip.protocol,
            proxy_url=ip.proxy_url,
            region=ip.region,
            ttl=ip.ttl,
            latency_ms=latency,
            leased=leased,
            created_at=created_at,
            last_verified_at=created_at,
        )

    return _make


@pytest.fixture
async def pool():
    """空 Level2Pool。"""
    return Level2Pool()


@pytest.fixture
async def mixed_pool(make_ip, make_l2):
    """预置多协议（HTTP/HTTPS/SOCKS4/SOCKS5/HTTP）租赁池，id 0..4，部分已租赁。

    - 0: http   10.0.0.1  空闲
    - 1: https  10.0.0.2  已租赁
    - 2: socks4 10.0.0.3  空闲
    - 3: socks5 10.0.0.4  已租赁
    - 4: http   10.0.0.5  空闲
    """
    p = Level2Pool()
    specs = [
        (1, Protocol.HTTP, False),
        (2, Protocol.HTTPS, True),
        (3, Protocol.SOCKS4, False),
        (4, Protocol.SOCKS5, True),
        (5, Protocol.HTTP, False),
    ]
    for idx, proto, leased in specs:
        rec = await p.upsert(make_l2(make_ip(idx, protocol=proto)))
        if leased:
            rec.leased = True
            rec.leased_at = 1100.0
    return p


# ---------------------------------------------------------------------------
# Tester 工厂（可注入站点测试/复验行为）
# ---------------------------------------------------------------------------


@pytest.fixture
def tester_factory():
    """Tester 工厂：``tester_factory(site_fn=..., revalidate_fn=..., threshold=...)``。"""
    from app.tester import Tester

    def _make(
        site_fn=None,
        revalidate_fn=None,
        threshold: int = 2000,
        target_url: str = "http://www.baidu.com",
        concurrency: int = 10,
    ) -> Tester:
        return Tester(
            target_url=target_url,
            threshold_ms=threshold,
            connect_timeout=1.0,
            concurrency=concurrency,
            site_fn=site_fn,
            revalidate_fn=revalidate_fn,
        )

    return _make


# ---------------------------------------------------------------------------
# SyncTask 排空辅助（拉取与测试解耦后，_sync_once 只入队）
# ---------------------------------------------------------------------------


@pytest.fixture
def drain_sync():
    """启动一个测试 worker 并排空 SyncTask 队列。

    ``drain_sync(task)``：启动 worker 后等待全部已入队批次测试完成；
    ``drain_sync(task, once=True)``：先执行一次 ``_sync_once()``（拉取+入队）
    再排空。结束前取消并回收 worker。
    """

    async def _drain(task, *, once: bool = False):
        worker = asyncio.create_task(task._run_worker())
        try:
            if once:
                await task._sync_once()
            await task.join()
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    return _drain


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
# 本地 mock 一级池 HTTP 服务（集成测试）
# ---------------------------------------------------------------------------


class MockLevel1State:
    """可变状态：测试中直接修改以控制模拟一级池行为（记录集、id 空间重置）。"""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self._seq = 0

    def reset(self) -> None:
        self.records = []
        self._seq = 0

    def add(self, count: int = 3, ip_prefix: str = "127.0.0.", protocol: str = "http",
            ttl: float | None = 120.0, region: str = "mock-region") -> None:
        """追加 ``count`` 条记录，id 单调递增。"""
        for _ in range(count):
            self._seq += 1
            octet = 1 + (self._seq % 250)
            port = 8000 + (self._seq // 250) % 1000
            ip = f"{ip_prefix}{octet}"
            self.records.append(
                {
                    "id": self._seq,
                    "ip": ip,
                    "port": port,
                    "protocol": protocol,
                    "proxy_url": f"{protocol}://{ip}:{port}",
                    "region": region,
                    "ttl": ttl,
                    "created_at": 1000.0,
                }
            )

    def reset_with(self, records: list[dict]) -> None:
        """整表替换并同步 id 序列（模拟一级池重启后 id 空间归零）。"""
        self.records = list(records)
        self._seq = max((r["id"] for r in records), default=0)


mock_level1_state = MockLevel1State()


def _make_mock_level1_app():
    from fastapi import FastAPI

    app = FastAPI(title="Mock Level1 Pool")

    @app.get("/api/v1/ips")
    async def ips():
        return {"code": 0, "msg": "ok", "data": mock_level1_state.records}

    @app.get("/api/v1/ips/after/{id_}")
    async def ips_after(id_: int):
        records = [r for r in mock_level1_state.records if r["id"] > id_]
        max_id = mock_level1_state.records[-1]["id"] if mock_level1_state.records else None
        return {
            "code": 0,
            "msg": "ok",
            "data": records,
            "max_id": max_id,
        }

    return app


mock_level1_app = _make_mock_level1_app()


@pytest.fixture
def level1_state():
    """mock 一级池可变状态对象（与 mock_level1_app 共用同一实例）。

    测试内通过 ``state.add(...)`` / ``state.reset_with(...)`` 控制记录集与 id 空间。
    """
    return mock_level1_state


@pytest.fixture
async def mock_level1_server():
    """在同一事件循环内启动 mock 一级池服务，yield 其 base URL。

    测试内通过修改 ``mock_level1_state`` 控制记录集与 id 空间（含重启模拟）。
    """
    import uvicorn

    mock_level1_state.reset()
    config = uvicorn.Config(
        mock_level1_app, host="127.0.0.1", port=0, log_level="error"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started, "mock level1 server failed to start"
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
        mock_level1_state.reset()