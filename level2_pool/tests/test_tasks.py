"""tasks.py 测试：同步周期 / 复验删除失败项（含租赁中）/ TTL 周期 / 异常隔离 / 取消。

覆盖测试计划书 L2-TASK-001 ~ 003。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.pool import Level2Pool, ServiceStats
from app.tasks import RevalidateTask, SyncTask, TtlSweeper


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


def test_sync_task_reexported():
    from app.syncer import SyncTask as _SyncTask

    assert SyncTask is _SyncTask


# ---------------------------------------------------------------------------
# RevalidateTask
# ---------------------------------------------------------------------------


async def test_revalidate_removes_dead_including_leased(
    pool, make_l2, make_ip, tester_factory
):
    """复验删除不通过项（含租赁中的），按策划书语义。"""
    for i in range(1, 4):
        await pool.upsert(make_l2(make_ip(i)))
    leased = await pool.acquire()  # 最新优先 → 10.0.0.3
    assert leased is not None and leased.leased

    def _reval(rec):
        return rec.ip == "10.0.0.1", 10.0

    tester = tester_factory(revalidate_fn=_reval)
    sleep_rec = _SleepRecorder(stop_after=2)
    task = RevalidateTask(pool, tester, interval=0.01, sleep_fn=sleep_rec)
    with pytest.raises(_StopLoop):
        await task.run()
    assert sleep_rec.durations == [0.01, 0.01]
    remaining = pool.all()
    assert [r.ip for r in remaining] == ["10.0.0.1"]
    assert pool.stats().leased_total == 0


async def test_revalidate_keeps_all_alive(pool, make_l2, make_ip, tester_factory):
    for i in range(1, 4):
        await pool.upsert(make_l2(make_ip(i)))
    tester = tester_factory(revalidate_fn=lambda rec: (True, 10.0))
    task = RevalidateTask(pool, tester, interval=0.01, sleep_fn=_SleepRecorder(stop_after=2))
    with pytest.raises(_StopLoop):
        await task.run()
    assert len(pool.all()) == 3


async def test_revalidate_exception_isolation(pool, make_l2, make_ip):
    for i in range(1, 4):
        await pool.upsert(make_l2(make_ip(i)))

    class _FlakyTester:
        def __init__(self):
            self.calls = 0

        async def revalidate(self, records):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return records

    flaky = _FlakyTester()
    stats = ServiceStats()
    task = RevalidateTask(
        pool, flaky, interval=0.01, stats=stats, sleep_fn=_SleepRecorder(stop_after=3)
    )
    with pytest.raises(_StopLoop):
        await task.run()
    assert flaky.calls == 2
    assert len(pool.all()) == 3  # 第一次整批抛错被隔离，记录不受影响
    assert stats.revalidate_failures == 1


async def test_revalidate_cancellation(pool, tester_factory):
    async def _never(duration):
        await asyncio.sleep(3600)

    task = RevalidateTask(pool, tester_factory(), 60.0, sleep_fn=_never)
    t = asyncio.create_task(task.run())
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t


async def test_revalidate_cancelled_during_tick(pool):
    """tick 内取消 → CancelledError 穿透（run 的 CancelledError 分支）。"""

    class _CancelTester:
        async def revalidate(self, records):
            raise asyncio.CancelledError()

    task = RevalidateTask(pool, _CancelTester(), 60.0, sleep_fn=_SleepRecorder(stop_after=None))
    with pytest.raises(asyncio.CancelledError):
        await task.run()


# ---------------------------------------------------------------------------
# TtlSweeper
# ---------------------------------------------------------------------------


async def test_ttl_sweeper_removes_expired(pool, make_l2, make_ip):
    now = time.time()
    await pool.upsert(make_l2(make_ip(1, ttl=5.0), created_at=now - 10.0))
    await pool.upsert(make_l2(make_ip(2, ttl=1000.0), created_at=now - 10.0))
    sleep_rec = _SleepRecorder(stop_after=2)
    sweeper = TtlSweeper(pool, 5.0, sleep_fn=sleep_rec)
    with pytest.raises(_StopLoop):
        await sweeper.run()
    assert sleep_rec.durations == [5.0, 5.0]
    assert [r.ip for r in pool.all()] == ["10.0.0.2"]


async def test_ttl_sweeper_exception_isolation(pool, make_l2, make_ip):
    now = time.time()
    await pool.upsert(make_l2(make_ip(1, ttl=5.0), created_at=now - 10.0))
    await pool.upsert(make_l2(make_ip(2, ttl=1000.0), created_at=now - 10.0))

    calls = []
    original = pool.sweep_ttl

    async def _flaky(now_):
        calls.append(now_)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return await original(now_)

    pool.sweep_ttl = _flaky
    stats = ServiceStats()
    sweeper = TtlSweeper(pool, 5.0, stats, sleep_fn=_SleepRecorder(stop_after=3))
    with pytest.raises(_StopLoop):
        await sweeper.run()
    assert len(calls) == 2
    assert [r.ip for r in pool.all()] == ["10.0.0.2"]
    assert stats.ttl_sweep_failures == 1


async def test_ttl_sweeper_cancellation(pool):
    async def _never(duration):
        await asyncio.sleep(3600)

    sweeper = TtlSweeper(pool, 5.0, sleep_fn=_never)
    t = asyncio.create_task(sweeper.run())
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t


async def test_ttl_sweeper_cancelled_during_sweep(pool):
    async def _cancel_sweep(now_):
        raise asyncio.CancelledError()

    pool.sweep_ttl = _cancel_sweep
    sweeper = TtlSweeper(pool, 5.0, sleep_fn=_SleepRecorder(stop_after=None))
    with pytest.raises(asyncio.CancelledError):
        await sweeper.run()