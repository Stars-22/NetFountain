"""Phase 4 测试 conftest：aioresponses 桩、多站点 registry/dispatcher、running_app、mock 二级池服务。

提供：
- aioresponses × aiohttp 3.14 兼容 shim（同 common/level2 测试）
- aio_mock（aioresponses 桩上下文）
- tmp_routes：临时路由配置文件
- registry：预置多站点路由表的 Registry
- dispatcher：指向 registry 的 Dispatcher（复用 aiohttp 桩会话）
- running_app：单事件循环内运行 FastAPI 应用的 async 上下文管理器
- mock_level2：本地 mock 二级池 HTTP 服务工厂（status/count/ips/acquire/release/delete/release-all）
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
import yaml
from aiohttp.http_writer import StreamWriter

from app.core.dispatcher import Dispatcher
from app.core.registry import Registry

# ---------------------------------------------------------------------------
# aioresponses 0.7.9 与 aiohttp 3.14 兼容 shim（同 common/level2 测试）
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
# 路由表 / 桩会话
# ---------------------------------------------------------------------------

ROUTE_YAML = """\
sites:
  - name: site_a
    base_url: http://127.0.0.1:8001
    target_url: https://www.example.com
  - name: site_b
    base_url: http://127.0.0.1:8002
    target_url: https://www.example.org
"""


def write_routes(path, sites: list[dict]) -> None:
    """把站点列表写入路由配置文件。"""
    path.write_text(yaml.safe_dump({"sites": sites}, sort_keys=False), encoding="utf-8")


@pytest.fixture
def aio_mock():
    """aioresponses 上下文：测试内通过 m.get(url, ...) 打桩。"""
    with aioresponses.aioresponses() as m:
        yield m


@pytest.fixture
def tmp_routes(tmp_path):
    """临时路由配置文件（两个示例站点）。"""
    p = tmp_path / "proxy_routes.yaml"
    write_routes(p, [
        {"name": "site_a", "base_url": "http://127.0.0.1:8001", "target_url": "https://www.example.com"},
        {"name": "site_b", "base_url": "http://127.0.0.1:8002", "target_url": "https://www.example.org"},
    ])
    return p


@pytest.fixture
async def registry(tmp_routes):
    """预置多站点路由表的 Registry。"""
    reg = Registry(route_file=str(tmp_routes))
    n = await reg.load()
    assert n == 2
    return reg


@pytest.fixture
async def mock_session(aio_mock):
    """基于 aioresponses 的 aiohttp 桩会话。"""
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture
async def dispatcher(registry, mock_session):
    """指向 registry 的 Dispatcher（复用桩会话，转发超时 2s）。"""
    return Dispatcher(registry, mock_session, timeout=2.0)


# ---------------------------------------------------------------------------
# 运行中的应用（单事件循环，供 routes/集成测试）
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _run_app(app):
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
# 本地 mock 二级池 HTTP 服务（集成测试）
# ---------------------------------------------------------------------------


class MockLevel2State:
    """可变状态：测试中直接修改以控制模拟二级池行为。"""

    def __init__(self, site: str) -> None:
        self.site = site
        self.pool: dict[int, dict] = {}
        self._seq = 0

    def add(self, count: int = 3, leased: bool = False) -> None:
        for _ in range(count):
            self.acquire(leased=leased)

    def acquire(self, leased: bool = True) -> dict:
        self._seq += 1
        rec = {
            "id": self._seq,
            "ip": f"10.0.0.{self._seq}",
            "port": 8000 + self._seq,
            "protocol": "http",
            "proxy_url": f"http://10.0.0.{self._seq}:{8000 + self._seq}",
            "latency_ms": float(self._seq * 10),
            "leased": leased,
            "ttl": 120.0,
            "created_at": 1000.0,
            "site": self.site,
        }
        self.pool[rec["id"]] = rec
        return rec


def _make_mock_level2_app(state: MockLevel2State):
    from fastapi import FastAPI

    app = FastAPI(title=f"Mock Level2 {state.site}")

    @app.get("/api/v1/status")
    async def status():
        return {"code": 0, "msg": "ok", "data": {"site": state.site, "pool_total": len(state.pool)}}

    @app.get("/api/v1/count")
    async def count():
        return {"code": 0, "msg": "ok", "data": {"total": len(state.pool)}}

    @app.get("/api/v1/ips")
    async def ips():
        return {"code": 0, "msg": "ok", "data": list(state.pool.values())}

    @app.post("/api/v1/ips/acquire")
    async def acquire():
        return {"code": 0, "msg": "ok", "data": state.acquire()}

    @app.post("/api/v1/ips/{id_}/release")
    async def release(id_: int):
        rec = state.pool.get(id_)
        if rec is None:
            return {"code": 40400, "msg": "record not found", "data": None}
        rec["leased"] = False
        return {"code": 0, "msg": "ok", "data": True}

    @app.delete("/api/v1/ips/{id_}")
    async def delete(id_: int):
        if id_ not in state.pool:
            return {"code": 40400, "msg": "record not found", "data": None}
        del state.pool[id_]
        return {"code": 0, "msg": "ok", "data": True}

    @app.post("/api/v1/ips/release-all")
    async def release_all():
        count = sum(1 for r in state.pool.values() if r["leased"])
        for r in state.pool.values():
            r["leased"] = False
        return {"code": 0, "msg": "ok", "data": count}

    return app


@pytest.fixture
def mock_level2():
    """mock 二级池服务工厂：``async with mock_level2("site_a", "site_b") as servers:``。

    每个服务 yield ``(site, state, base_url)`` 三元组；退出时统一关闭。
    """

    @asynccontextmanager
    async def _start(*sites):
        import uvicorn

        servers: list[tuple] = []
        results: list[tuple] = []
        try:
            for name in sites:
                state = MockLevel2State(name)
                app = _make_mock_level2_app(state)
                config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
                server = uvicorn.Server(config)
                task = asyncio.create_task(server.serve())
                for _ in range(200):
                    if server.started:
                        break
                    await asyncio.sleep(0.01)
                assert server.started, f"mock level2 {name} failed to start"
                port = server.servers[0].sockets[0].getsockname()[1]
                servers.append((server, task))
                results.append((name, state, f"http://127.0.0.1:{port}"))
            yield results
        finally:
            for server, task in servers:
                server.should_exit = True
                try:
                    await asyncio.wait_for(task, timeout=3)
                except asyncio.TimeoutError:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    return _start