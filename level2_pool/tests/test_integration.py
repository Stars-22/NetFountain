"""集成测试：mock 一级池 → 同步 → 站点测试 → 入池 → acquire 闭环。

覆盖测试计划书 L2-INT-001 ~ 004：
- 闭环：mock 一级池 → 同步 → 站点测试 → 入池 → acquire
- 一级池重启（id 归零）→ 空响应触发全量重拉、服务无感恢复
- acquire/release 完整流程
- 同步 + 复验 + TTL 三任务并发运行互不干扰
"""
from __future__ import annotations

import asyncio
import time

import aiohttp
import pytest

from app.config import (
    Level1Config,
    Level2Settings,
    ServiceConfig,
    SiteConfig,
    SyncConfig,
)
from app.main import create_app
from app.pool import Level2Pool, ServiceStats
from app.syncer import Level1SyncClient, SyncTask
from app.tasks import RevalidateTask, TtlSweeper
from app.tester import Tester as _Tester


class _StopLoop(Exception):
    pass


class _SleepRecorder:
    """记录每次 sleep 时长，可设置 stop_after 次后抛 _StopLoop 终止循环。"""

    def __init__(self, stop_after: int | None = None):
        self.durations: list[float] = []
        self._stop_after = stop_after

    async def __call__(self, duration: float):
        self.durations.append(duration)
        if self._stop_after is not None and len(self.durations) >= self._stop_after:
            raise _StopLoop()


def _always_pass_tester():
    return _Tester(
        target_url="http://www.baidu.com",
        threshold_ms=2000,
        connect_timeout=1.0,
        concurrency=5,
        site_fn=lambda rec: (True, 50.0),
        revalidate_fn=lambda rec: (True, 50.0),
    )


def _integration_settings(base: str, interval: float = 0.05):
    return Level2Settings(
        service=ServiceConfig(host="127.0.0.1", port=0),
        site=SiteConfig(name="site_a", target_url="http://www.baidu.com"),
        level1=Level1Config(base_url=base),
        sync=SyncConfig(interval=interval, timeout=2.0),
        test={"latency_threshold_ms": 2000, "connect_timeout": 1.0, "concurrency": 5},
        revalidate_interval=60.0,
        ttl_sweep_interval=5.0,
    )


async def _wait_for_pool(client, min_total: int, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = (await client.get("/api/v1/status")).json()["data"]
        if data["pool_stats"]["total"] >= min_total:
            return data
        await asyncio.sleep(0.05)
    raise AssertionError(f"pool did not reach {min_total} records within {timeout}s")


async def test_integration_closed_loop(mock_level1_server, running_app, level1_state):
    level1_state.add(3)
    app = create_app(
        _integration_settings(mock_level1_server),
        tester=_always_pass_tester(),
        start_tasks=True,
    )
    async with running_app(app) as client:
        status = await _wait_for_pool(client, min_total=3)
        assert status["total_pulled"] >= 3
        assert status["total_entered"] == status["total_pulled"]
        assert status["last_synced_id"] == 3

        ips = (await client.get("/api/v1/ips")).json()["data"]
        assert len(ips) == 3
        assert all(rec["leased"] is False for rec in ips)

        acq = (await client.post("/api/v1/ips/acquire")).json()
        assert acq["code"] == 0
        assert acq["data"]["leased"] is True

        count = (await client.get("/api/v1/count")).json()["data"]
        assert count["total"] == 3
        assert count["leased_total"] == 1
        assert count["free_total"] == 2


async def test_integration_restart_recovers(mock_level1_server, level1_state, drain_sync):
    """一级池重启（id 归零）→ 空响应触发全量重拉、服务无感恢复。"""
    level1_state.add(3)  # ids 1,2,3
    session = aiohttp.ClientSession()
    try:
        client = Level1SyncClient(mock_level1_server, session, timeout=2.0)
        pool = Level2Pool()
        stats = ServiceStats()
        task = SyncTask(client, _always_pass_tester(), pool, stats, interval=0.01)
        await drain_sync(task, once=True)
        assert task.last_synced_id == 3
        assert len(pool.all()) == 3

        leased = await pool.acquire()
        assert leased is not None and leased.leased

        level1_state.reset_with(
            [
                {
                    "id": 1,
                    "ip": "192.168.0.1",
                    "port": 9001,
                    "protocol": "http",
                    "proxy_url": "http://192.168.0.1:9001",
                    "region": "CN",
                    "ttl": 120.0,
                    "created_at": 1000.0,
                },
                {
                    "id": 2,
                    "ip": "192.168.0.2",
                    "port": 9002,
                    "protocol": "http",
                    "proxy_url": "http://192.168.0.2:9002",
                    "region": "CN",
                    "ttl": 120.0,
                    "created_at": 1000.0,
                },
            ]
        )
        # after(3) → 空 → 全量重拉 ids 1,2
        await drain_sync(task, once=True)
        assert task.last_synced_id == 2
        assert len(pool.all()) == 5  # 旧 3 条保留 + 新 2 条
        assert leased.leased is True  # 租赁状态无感
        assert await pool.release(leased.id) is True
    finally:
        await session.close()


async def test_integration_no_new_ips_skips_full_repull(mock_level1_server, level1_state, drain_sync):
    """一级池暂无新 IP（after 空且 max_id == 水位线）→ 不触发全量重拉。"""
    level1_state.add(3)  # ids 1,2,3
    session = aiohttp.ClientSession()
    try:
        client = Level1SyncClient(mock_level1_server, session, timeout=2.0)
        pool = Level2Pool()
        stats = ServiceStats()
        task = SyncTask(client, _always_pass_tester(), pool, stats, interval=0.01)
        await drain_sync(task, once=True)
        assert task.last_synced_id == 3
        assert stats.total_pulled == 3
        await drain_sync(task, once=True)
        assert task.last_synced_id == 3
        assert stats.total_pulled == 3  # 未发生全量重拉
        assert len(pool.all()) == 3
    finally:
        await session.close()


async def test_integration_acquire_release_flow(mock_level1_server, running_app, level1_state):
    level1_state.add(2)
    app = create_app(
        _integration_settings(mock_level1_server),
        tester=_always_pass_tester(),
        start_tasks=True,
    )
    async with running_app(app) as client:
        await _wait_for_pool(client, min_total=2)
        acq1 = (await client.post("/api/v1/ips/acquire")).json()["data"]
        acq2 = (await client.post("/api/v1/ips/acquire")).json()["data"]
        assert acq1["id"] != acq2["id"]

        empty = (await client.post("/api/v1/ips/acquire")).json()
        assert empty["code"] == 40402

        rel = await client.post(f"/api/v1/ips/{acq1['id']}/release")
        assert rel.json()["code"] == 0

        acq3 = (await client.post("/api/v1/ips/acquire")).json()["data"]
        assert acq3["id"] == acq1["id"]

        # release-all 后再可获取
        await client.post("/api/v1/ips/release-all")
        acq4 = (await client.post("/api/v1/ips/acquire")).json()
        assert acq4["code"] == 0


async def test_integration_three_tasks_concurrent(mock_level1_server, level1_state, drain_sync):
    """同步 + 复验 + TTL 三任务并发运行，互不干扰、计数一致。"""
    level1_state.add(3, ttl=120.0)
    session = aiohttp.ClientSession()
    try:
        client = Level1SyncClient(mock_level1_server, session, timeout=2.0)
        pool = Level2Pool()
        stats = ServiceStats()
        tester = _always_pass_tester()
        sync = SyncTask(client, tester, pool, stats, interval=0.02, sleep_fn=_SleepRecorder(stop_after=4))
        reval = RevalidateTask(pool, tester, interval=0.02, sleep_fn=_SleepRecorder(stop_after=4))
        ttl = TtlSweeper(pool, interval=0.02, sleep_fn=_SleepRecorder(stop_after=4))
        results = await asyncio.gather(sync.run(), reval.run(), ttl.run(), return_exceptions=True)
        assert all(isinstance(r, Exception) for r in results)
        await drain_sync(sync)  # 排空同步入队的批次后再校验计数
        # 计数一致：每个被拉取的都通过站点测试入池；池内为全部不同记录
        assert stats.total_pulled == stats.total_entered
        assert stats.total_pulled >= 3
        assert pool.stats().total == 3
        assert pool.stats().by_proto == {"http": 3}
    finally:
        await session.close()


async def test_lifespan_defaults_construct_real_tester(
    mock_level1_server, running_app, level1_state
):
    """未注入 tester 时 lifespan 按配置创建真实 Tester（真实站点测试对假代理失败，不入池）。"""
    level1_state.add(2)
    settings = _integration_settings(mock_level1_server, interval=0.05)
    app = create_app(settings, start_tasks=True)
    async with running_app(app) as client:
        await asyncio.sleep(0.3)
        data = (await client.get("/api/v1/status")).json()["data"]
        # 任务已启动、API 可响应；假代理站点测试失败故 pool 为空
        assert data["api_call_count"] >= 1
        assert data["total_pulled"] >= 2
        assert data["pool_stats"]["total"] == 0
    assert isinstance(app.state.pool, Level2Pool)