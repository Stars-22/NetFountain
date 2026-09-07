"""性能测试（marker=perf，默认跳过，使用 -m perf 运行）。

- L1-PERF-001：持续拉取入池吞吐 ≥10/s 且池容量有界（内存不增长）
- L1-PERF-002：500 容量池 /ips、/count 查询延迟 <10ms
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.config import Level1Settings
from app.core.pool import Level1Pool
from app.core.stats import ServiceStats
from app.main import create_app
from app.tasks import PullTask
from app.testing import tester as tester_mod
from ip_pool_common.models import Protocol, ProviderIp


class _FastProvider:
    def __init__(self):
        self._n = 0

    async def pull(self, count):
        out = []
        for i in range(count):
            self._n += 1
            out.append(
                ProviderIp(
                    ip=f"192.168.{self._n % 200}.{self._n % 250}",
                    port=8000 + i,
                    protocol=Protocol.HTTP,
                )
            )
        return out


async def _pass(ip):
    return True, 1.0


@pytest.mark.perf
async def test_pull_throughput_and_bounded_memory():
    provider = _FastProvider()
    tester = tester_mod.Tester(timeout=1.0, concurrency=10, test_fn=_pass)
    pool = Level1Pool(max_size=50)
    stats = ServiceStats()
    lock = asyncio.Lock()
    task = PullTask(provider, tester, pool, stats, 10, 0.05, lock)
    t = asyncio.create_task(task.run())
    await asyncio.sleep(2.0)
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)
    rate = stats.total_entered / 2.0
    assert rate >= 10, f"entry rate too low: {rate:.1f}/s"
    assert pool.size() == 50
    assert len(await pool.all()) == 50


@pytest.mark.perf
async def test_query_latency_on_500_pool():
    pool = Level1Pool(max_size=500)
    now = time.time()
    for i in range(500):
        await pool.add(
            ProviderIp(
                ip=f"10.1.{i // 250}.{i % 250}",
                port=8000 + i,
                protocol=Protocol.HTTP,
                region="CN",
                ttl=120,
            ),
            now,
        )
    app = create_app(Level1Settings(), pool=pool, start_tasks=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/v1/count")  # warm up（构建中间件栈）
        t0 = time.perf_counter()
        r1 = await client.get("/api/v1/ips")
        t_ips = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        r2 = await client.get("/api/v1/count")
        t_count = (time.perf_counter() - t0) * 1000
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert len(r1.json()["data"]) == 500
    assert t_ips < 10, f"/ips latency too high: {t_ips:.2f}ms"
    assert t_count < 10, f"/count latency too high: {t_count:.2f}ms"