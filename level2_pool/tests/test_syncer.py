"""syncer.py 测试：全量/增量/空响应重置/异常隔离/水位线推进。

覆盖测试计划书 L2-SYNC-001 ~ 008。
"""
from __future__ import annotations

import asyncio

import aiohttp
import pytest

from app.pool import Level2Pool, ServiceStats
from app.syncer import Level1SyncClient, SyncTask

BASE = "http://level1.test"

_OK = {"code": 0, "msg": "ok"}


def _payload(*records: dict) -> dict:
    return {**_OK, "data": list(records)}


def _ip_dict(idx: int, id_: int | None = None, protocol: str = "http") -> dict:
    pid = id_ if id_ is not None else idx
    return {
        "id": pid,
        "ip": f"1.2.3.{idx}",
        "port": 8080 + idx,
        "protocol": protocol,
        "proxy_url": f"{protocol}://1.2.3.{idx}:{8080 + idx}",
        "region": "CN",
        "ttl": 120.0,
        "created_at": 1000.0,
    }


async def _make_task(mock_session, *, site_fn=None, interval=3.0, sleep_fn=None, stats=None):
    session, _ = mock_session
    client = Level1SyncClient(BASE, session, timeout=2.0)
    pool = Level2Pool()
    if stats is None:
        stats = ServiceStats()
    from app.tester import Tester

    tester = Tester(
        target_url="http://www.baidu.com",
        threshold_ms=2000,
        connect_timeout=1.0,
        concurrency=5,
        site_fn=site_fn or (lambda rec: (True, 100.0)),
    )
    task = SyncTask(client, tester, pool, stats, interval=interval, sleep_fn=sleep_fn)
    return client, pool, stats, tester, task


# ---------------------------------------------------------------------------
# Level1SyncClient 契约
# ---------------------------------------------------------------------------


async def test_client_fetch_all(mock_session):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(1), _ip_dict(2)))
    client = Level1SyncClient(BASE, session)
    records = await client.fetch_all()
    assert [r.id for r in records] == [1, 2]
    assert records[0].proxy_url == "http://1.2.3.1:8081"
    assert records[0].protocol.value == "http"
    assert records[0].region == "CN"
    assert records[0].ttl == 120.0


async def test_client_fetch_all_empty(mock_session):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload())
    client = Level1SyncClient(BASE, session)
    assert await client.fetch_all() == []


async def test_client_fetch_after(mock_session):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips/after/5", payload=_payload(_ip_dict(6, id_=6)))
    client = Level1SyncClient(BASE, session)
    records = await client.fetch_after(5)
    assert [r.id for r in records] == [6]


async def test_client_fetch_after_empty(mock_session):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips/after/5", payload=_payload())
    client = Level1SyncClient(BASE, session)
    assert await client.fetch_after(5) == []


async def test_client_fetch_after_proxy_url_rebuilt(mock_session):
    session, m = mock_session
    item = _ip_dict(1)
    item.pop("proxy_url")
    m.get(f"{BASE}/api/v1/ips", payload=_payload(item))
    client = Level1SyncClient(BASE, session)
    records = await client.fetch_all()
    assert records[0].proxy_url == "http://1.2.3.1:8081"


async def test_client_non_200_raises(mock_session):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", status=500)
    client = Level1SyncClient(BASE, session)
    with pytest.raises(aiohttp.ClientResponseError):
        await client.fetch_all()


async def test_client_non_object_payload_raises(mock_session):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=[1, 2, 3])
    client = Level1SyncClient(BASE, session)
    with pytest.raises(ValueError):
        await client.fetch_all()


async def test_client_missing_data_key_returns_empty(mock_session):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload={"code": 0, "msg": "ok"})
    client = Level1SyncClient(BASE, session)
    assert await client.fetch_all() == []


async def test_client_data_not_list_raises(mock_session):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload={"code": 0, "msg": "ok", "data": "not-a-list"})
    client = Level1SyncClient(BASE, session)
    with pytest.raises(ValueError):
        await client.fetch_all()


# ---------------------------------------------------------------------------
# SyncTask
# ---------------------------------------------------------------------------


async def test_first_sync_full_and_watermark(mock_session, drain_sync):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(1), _ip_dict(2), _ip_dict(3)))
    _, pool, stats, _, task = await _make_task(mock_session)
    await drain_sync(task, once=True)
    assert task.last_synced_id == 3
    assert stats.last_synced_id == 3
    assert stats.total_pulled == 3
    assert stats.total_entered == 3
    assert len(pool.all()) == 3


async def test_incremental_advances_watermark(mock_session, drain_sync):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(1), _ip_dict(2)))
    m.get(f"{BASE}/api/v1/ips/after/2", payload=_payload(_ip_dict(3, id_=3), _ip_dict(4, id_=4)))
    _, pool, stats, _, task = await _make_task(mock_session)
    await drain_sync(task, once=True)
    assert task.last_synced_id == 2
    await drain_sync(task, once=True)
    assert task.last_synced_id == 4
    assert stats.total_pulled == 4
    assert len(pool.all()) == 4


async def test_empty_response_triggers_full_repull(mock_session, drain_sync):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(1), _ip_dict(2), _ip_dict(3)))
    # 一级池重启：after 返回空，全量返回新的低 id 记录（含新身份）
    m.get(f"{BASE}/api/v1/ips/after/3", payload=_payload())
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(1, id_=1), _ip_dict(2, id_=2), _ip_dict(4, id_=3)))
    _, pool, _, _, task = await _make_task(mock_session)
    await drain_sync(task, once=True)
    assert task.last_synced_id == 3
    await drain_sync(task, once=True)
    # 空响应 → 全量重拉 → 新记录（1.2.3.4）入池，水位线重置为 3
    assert task.last_synced_id == 3
    assert [r.ip for r in pool.all()] == ["1.2.3.1", "1.2.3.2", "1.2.3.3", "1.2.3.4"]


async def test_empty_response_resets_watermark(mock_session, drain_sync):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(1), _ip_dict(2), _ip_dict(3)))
    # after(3) 空 → 全量只剩 id 1、2（模拟 id 空间归零）
    m.get(f"{BASE}/api/v1/ips/after/3", payload=_payload())
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(3, id_=1), _ip_dict(4, id_=2)))
    _, pool, _, _, task = await _make_task(mock_session)
    await drain_sync(task, once=True)
    assert task.last_synced_id == 3
    await drain_sync(task, once=True)
    assert task.last_synced_id == 2
    assert len(pool.all()) == 4  # 旧 3 条保留 + 新增 1 条（1.2.3.4）


async def test_reset_does_not_remove_existing_records(mock_session, drain_sync):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(1), _ip_dict(2)))
    m.get(f"{BASE}/api/v1/ips/after/2", payload=_payload())
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(3, id_=1), _ip_dict(4, id_=2)))
    _, pool, _, _, task = await _make_task(mock_session)
    await drain_sync(task, once=True)
    old_ids = {r.id for r in pool.all()}
    await drain_sync(task, once=True)
    assert pool.stats().total == 4
    assert {r.id for r in pool.all()} >= old_ids


async def test_reset_keeps_leased_records(mock_session, drain_sync):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(1), _ip_dict(2)))
    m.get(f"{BASE}/api/v1/ips/after/2", payload=_payload())
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(3, id_=1), _ip_dict(4, id_=2)))
    _, pool, _, _, task = await _make_task(mock_session)
    await drain_sync(task, once=True)
    leased = await pool.acquire()
    assert leased is not None
    await drain_sync(task, once=True)
    assert leased.leased is True
    assert leased in pool.all()


async def test_site_filter_threshold_applies(mock_session, drain_sync):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(1), _ip_dict(2), _ip_dict(3)))

    def _site(rec):
        lat = {"1.2.3.1": 100.0, "1.2.3.2": 2500.0, "1.2.3.3": 1999.0}[rec.ip]
        return True, lat

    _, pool, stats, _, task = await _make_task(mock_session, site_fn=_site)
    await drain_sync(task, once=True)
    assert [r.ip for r in pool.all()] == ["1.2.3.1", "1.2.3.3"]
    assert stats.total_pulled == 3
    assert stats.total_entered == 2


async def test_first_full_empty_keeps_none(mock_session, drain_sync):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload())
    _, pool, stats, _, task = await _make_task(mock_session)
    await drain_sync(task, once=True)
    assert task.last_synced_id is None
    assert stats.last_synced_id is None
    assert pool.stats().total == 0


async def test_sync_error_does_not_affect_pool(mock_session, drain_sync):
    """一级池不可达：现有记录不受影响，循环继续重试。"""
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(1), _ip_dict(2)))
    m.get(f"{BASE}/api/v1/ips/after/2", status=500)
    m.get(f"{BASE}/api/v1/ips/after/2", payload=_payload(_ip_dict(3, id_=3)))

    sleeps = []

    async def _sleep(duration):
        sleeps.append(duration)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    _, pool, stats, _, task = await _make_task(
        mock_session, interval=0.01, sleep_fn=_sleep
    )
    await drain_sync(task, once=True)  # tick1 健康，入池 2 条
    assert len(pool.all()) == 2

    with pytest.raises(asyncio.CancelledError):
        await task.run()
    await drain_sync(task)  # 排空 tick3 恢复后入队的批次
    # tick2 失败（不影响池），tick3 恢复后新增
    assert len(pool.all()) == 3
    assert task.last_synced_id == 3
    assert stats.total_pulled == 3
    assert stats.sync_failures >= 1


async def test_sync_task_run_interval(mock_session, drain_sync):
    """同步周期 ≈ sync_interval。"""
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(1)))
    m.get(f"{BASE}/api/v1/ips/after/1", payload=_payload(_ip_dict(1, id_=1)))

    sleeps = []

    async def _sleep(duration):
        sleeps.append(duration)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    _, pool, stats, _, task = await _make_task(
        mock_session, interval=1.5, sleep_fn=_sleep
    )
    with pytest.raises(asyncio.CancelledError):
        await task.run()
    await drain_sync(task)  # 排空两次拉取入队的批次
    assert len(sleeps) == 2
    assert all(d == pytest.approx(1.5, abs=0.05) for d in sleeps)
    assert stats.total_pulled == 2
    assert len(pool.all()) == 1  # 同一身份去重


async def test_sync_task_cancellation(mock_session, drain_sync):
    session, m = mock_session
    m.get(f"{BASE}/api/v1/ips", payload=_payload(_ip_dict(1)))
    _, pool, _, _, task = await _make_task(mock_session, interval=1.0)

    async def _never(duration):
        await asyncio.sleep(3600)

    task._sleep = _never
    t = asyncio.create_task(task.run())
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    await drain_sync(task)  # 排空取消前入队的批次
    assert len(pool.all()) == 1


async def test_sync_task_cancelled_during_tick(mock_session):
    """tick 内取消 → CancelledError 穿透（run 的 CancelledError 分支）。"""
    _, pool, _, _, task = await _make_task(mock_session, interval=1.0)

    async def _cancel_tick():
        raise asyncio.CancelledError()

    task._sync_once = _cancel_tick
    with pytest.raises(asyncio.CancelledError):
        await task.run()


# ---------------------------------------------------------------------------
# 拉取-测试解耦的多 worker 并发测试管线
# ---------------------------------------------------------------------------


class _ConcTester:
    def __init__(self, concurrency: int):
        self.concurrency = concurrency


def _task_for(tester, *, buffer_size: int = 20, test_workers: int | None = None):
    return SyncTask(
        object(),
        tester,
        Level2Pool(),
        ServiceStats(),
        interval=1.0,
        buffer_size=buffer_size,
        test_workers=test_workers,
    )


def test_sync_worker_count_auto():
    """默认 worker 数 = max(1, test_concurrency // 10)。"""
    assert _task_for(_ConcTester(50))._worker_count() == 5
    assert _task_for(_ConcTester(10))._worker_count() == 1
    assert _task_for(_ConcTester(5))._worker_count() == 1
    assert _task_for(object())._worker_count() == 1


def test_sync_worker_count_clamped():
    """显式 test_workers 超过安全上限时按 max(1, concurrency//10) 截断。"""
    base = _ConcTester(50)
    assert _task_for(base, test_workers=10)._worker_count() == 5
    assert _task_for(base, test_workers=2)._worker_count() == 2
    assert _task_for(base, test_workers=0)._worker_count() == 5
    assert _task_for(base, test_workers=-3)._worker_count() == 5


async def test_sync_worker_exception_isolation(make_ip, make_l2):
    class _FlakyTester:
        concurrency = 10

        def __init__(self):
            self.calls = 0

        async def site_filter(self, records):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return records

    task = _task_for(_FlakyTester(), test_workers=1)
    worker = asyncio.create_task(task._run_worker())
    try:
        task._enqueue([make_l2(make_ip(1))])
        task._enqueue([make_l2(make_ip(2))])
        await task.join()
        assert task._stats.total_entered == 1
        assert task._stats.test_failures == 1
        assert [r.ip for r in task._pool.all()] == ["10.0.0.2"]
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_sync_worker_drop_oldest_when_full(make_ip, make_l2):
    """队满时丢弃最旧待测批次，内存有界且 drops 计数。"""
    class _PassTester:
        concurrency = 10

        async def site_filter(self, records):
            return records

    task = _task_for(_PassTester(), test_workers=1, buffer_size=2)
    worker = asyncio.create_task(task._run_worker())
    try:
        for i in range(1, 5):
            task._enqueue([make_l2(make_ip(i))])
        await task.join()
        assert sorted(r.ip for r in task._pool.all()) == ["10.0.0.3", "10.0.0.4"]
        assert task.drops == 2
        assert task._stats.drops == 2
        assert task._stats.total_entered == 2
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_sync_multiple_workers_parallel_and_capped(make_ip, make_l2):
    """多 worker 并行消费多批，且同时测试的批数不超过 worker 数。"""
    active = 0
    max_active = 0

    class _TrackTester:
        concurrency = 50

        async def site_filter(self, records):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.03)
            active -= 1
            return records

    task = _task_for(_TrackTester(), test_workers=3)
    assert task._worker_count() == 3  # safe=50//10=5，test_workers=3 截断为 3
    workers = [
        asyncio.create_task(task._run_worker())
        for _ in range(task._worker_count())
    ]
    try:
        for i in range(4):
            task._enqueue([make_l2(make_ip(i * 10 + j)) for j in range(5)])
        await task.join()
        assert max_active > 1       # 多批并行
        assert max_active <= 3      # 不超过 worker 数
        assert task._stats.total_entered == 20
        assert len(task._pool.all()) == 20
    finally:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


async def test_sync_slow_test_does_not_block_pull_cadence(make_ip):
    """关键回归：测试 worker 被卡住时，拉取循环仍按节奏推进且队列有界。"""
    gate = asyncio.Event()

    class _SlowTester:
        concurrency = 50

        async def site_filter(self, records):
            await gate.wait()  # 永不完成
            return records

    class _SteadyClient:
        def __init__(self):
            self.calls = 0

        async def fetch_all(self):
            self.calls += 1
            return [make_ip(self.calls)]

        async def fetch_after(self, id_):
            return await self.fetch_all()

    task = SyncTask(
        _SteadyClient(), _SlowTester(), Level2Pool(), ServiceStats(),
        interval=0.005, buffer_size=100, test_workers=2,
    )
    t = asyncio.create_task(task.run())
    try:
        await asyncio.sleep(0.1)
        assert task._client.calls >= 5
        assert task._stats.total_pulled >= 5
        assert task._queue.qsize() <= 100
    finally:
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        gate.set()