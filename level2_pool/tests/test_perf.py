"""性能测试（marker: perf，默认跳过，用 -m perf 运行）。

覆盖 L2-PERF-001：百并发 acquire 无重复分配、延迟可接受；
L2-PERF-002：持续同步无积压。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.pool import Level2Pool, ServiceStats
from app.syncer import Level1SyncClient, SyncTask
from app.tester import Tester as _Tester

BASE = "http://level1.test"


def _payload(*records: dict) -> dict:
    return {"code": 0, "msg": "ok", "data": list(records)}


def _ip_dict(idx: int) -> dict:
    return {
        "id": idx,
        "ip": f"1.2.3.{idx % 250}",
        "port": 8080 + idx,
        "protocol": "http",
        "proxy_url": f"http://1.2.3.{idx % 250}:{8080 + idx}",
        "region": "CN",
        "ttl": 120.0,
        "created_at": 1000.0,
    }


@pytest.mark.perf
async def test_perf_100_concurrent_acquire(pool, make_l2, make_ip):
    n = 100
    for i in range(1, n + 1):
        await pool.upsert(make_l2(make_ip(i)))
    start = time.perf_counter()
    results = await asyncio.gather(*(pool.acquire() for _ in range(n)))
    elapsed = time.perf_counter() - start
    ids = [r.id for r in results if r is not None]
    assert len(ids) == n
    assert len(set(ids)) == n  # 无重复分配
    assert pool.stats().leased_total == n
    assert elapsed < 2.0


@pytest.mark.perf
async def test_perf_sustained_sync_no_backlog(mock_session, drain_sync):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload(*(_ip_dict(i) for i in range(1, 101))))
    for tick in range(1, 6):
        start = tick * 100
        m.get(
            f"{BASE}/api/v1/ips/after/{start}",
            payload=_payload(*(_ip_dict(i) for i in range(start + 1, start + 101))),
        )

    client = Level1SyncClient(BASE, session, timeout=2.0)
    pool = Level2Pool()
    stats = ServiceStats()
    tester = _Tester(
        target_url="http://www.baidu.com",
        threshold_ms=2000,
        connect_timeout=1.0,
        concurrency=50,
        site_fn=lambda rec: (True, 50.0),
    )
    task = SyncTask(client, tester, pool, stats, interval=0.01)
    start = time.perf_counter()
    for _ in range(6):
        await task._sync_once()
    await drain_sync(task)  # 排空全部批次后统计
    elapsed = time.perf_counter() - start
    assert pool.stats().total == 600
    assert stats.total_pulled == 600
    assert stats.total_entered == 600
    assert elapsed < 3.0