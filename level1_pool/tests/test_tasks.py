"""tasks.py 测试：拉取节奏 / 异常隔离 / 统计 / 并发测试管线 / TTL 清扫 / 取消。"""
from __future__ import annotations

import asyncio
import time

import pytest

import app.tester as tester_mod
from app.pool import Level1Pool, ServiceStats
from app.tasks import PullTask, TtlSweeper


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


class _FakeProvider:
    def __init__(self, responses):
        self._responses = responses
        self.pull_calls = 0

    async def pull(self, count):
        idx = min(self.pull_calls, len(self._responses) - 1)
        resp = self._responses[idx]
        self.pull_calls += 1
        if isinstance(resp, Exception):
            raise resp
        return resp


class _FakeTester:
    def __init__(self, passed_count):
        self._passed_count = passed_count

    async def test_many(self, ips):
        return ips[: self._passed_count]


async def _run_until_cancelled(task: PullTask, settle: float = 0.05) -> None:
    """以子任务运行拉取循环，等待若干 tick 并排空测试管线后取消。"""
    t = asyncio.create_task(task.run())
    await asyncio.sleep(settle)
    await task.join()
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t


# ---------------------------------------------------------------------------
# 拉取循环
# ---------------------------------------------------------------------------


async def test_pull_cadence_keeps_interval(make_ip):
    """tick 之间按 pull_interval 节奏（起点计时），不被测试耗时拖慢。"""
    provider = _FakeProvider([[make_ip(1), make_ip(2)]])
    tester = _FakeTester(passed_count=2)
    pool = Level1Pool(max_size=100)
    stats = ServiceStats()
    lock = asyncio.Lock()
    sleep_rec = _SleepRecorder(stop_after=2)
    task = PullTask(provider, tester, pool, stats, 10, 1.5, lock, sleep_fn=sleep_rec)
    with pytest.raises(_StopLoop):
        await task.run()
    assert len(sleep_rec.durations) == 2
    assert all(d == pytest.approx(1.5, abs=0.05) for d in sleep_rec.durations)
    assert provider.pull_calls == 2


async def test_pull_exception_does_not_stop_loop(make_ip):
    provider = _FakeProvider([RuntimeError("boom"), [make_ip(1)], [make_ip(2)]])
    tester = _FakeTester(passed_count=1)
    pool = Level1Pool(max_size=100)
    stats = ServiceStats()
    lock = asyncio.Lock()
    task = PullTask(provider, tester, pool, stats, 10, 0.01, lock)
    await _run_until_cancelled(task)
    assert provider.pull_calls >= 2
    assert stats.total_pulled == provider.pull_calls - 1
    assert stats.total_entered == stats.total_pulled
    assert pool.size() + pool.duplicates == stats.total_entered
    assert 1 <= pool.size() <= stats.total_entered


async def test_stats_total_pulled_and_entered(make_ip):
    provider = _FakeProvider([[make_ip(1), make_ip(2)]])
    tester = _FakeTester(passed_count=1)
    pool = Level1Pool(max_size=1000)
    stats = ServiceStats()
    lock = asyncio.Lock()
    task = PullTask(provider, tester, pool, stats, 10, 0.01, lock)
    await _run_until_cancelled(task)
    assert stats.total_pulled == provider.pull_calls * 2
    assert stats.total_entered == provider.pull_calls * 1
    assert pool.size() == 1
    assert pool.size() + pool.duplicates == stats.total_entered


async def test_pull_task_cancellation(make_ip):
    provider = _FakeProvider([[make_ip(1)]])
    tester = _FakeTester(passed_count=1)
    pool = Level1Pool(max_size=100)
    stats = ServiceStats()
    lock = asyncio.Lock()

    async def _never(duration):
        await asyncio.sleep(3600)

    task = PullTask(provider, tester, pool, stats, 10, 1.0, lock, sleep_fn=_never)
    t = asyncio.create_task(task.run())
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert stats.total_pulled == 1
    assert 0 <= pool.size() <= 1


# ---------------------------------------------------------------------------
# 并发测试管线
# ---------------------------------------------------------------------------


async def test_worker_adds_passed_in_order(make_ip):
    pool = Level1Pool(max_size=100)
    stats = ServiceStats()
    task = PullTask(
        _FakeProvider([[]]), _FakeTester(passed_count=2), pool, stats, 10, 0.5,
        asyncio.Lock(),
    )
    worker = asyncio.create_task(task._run_worker())
    try:
        task._enqueue([make_ip(1), make_ip(2), make_ip(3)])
        task._enqueue([make_ip(4), make_ip(5)])
        await task.join()
        records = await pool.all()
        assert [r.ip for r in records] == [
            "10.0.0.1",
            "10.0.0.2",
            "10.0.0.4",
            "10.0.0.5",
        ]
        assert stats.total_entered == 4
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_worker_exception_isolation(make_ip):
    class _FlakyTester:
        def __init__(self):
            self.calls = 0

        async def test_many(self, ips):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return ips

    pool = Level1Pool(max_size=100)
    stats = ServiceStats()
    task = PullTask(
        _FakeProvider([[]]), _FlakyTester(), pool, stats, 10, 0.5, asyncio.Lock()
    )
    worker = asyncio.create_task(task._run_worker())
    try:
        task._enqueue([make_ip(1)])
        task._enqueue([make_ip(2)])
        await task.join()
        assert stats.total_entered == 1
        records = await pool.all()
        assert [r.id for r in records] == [0]
        assert [r.ip for r in records] == ["10.0.0.2"]
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_worker_drop_oldest_when_full(make_ip):
    """队满时丢弃最旧待测批次，内存有界且 drops 计数。"""
    pool = Level1Pool(max_size=100)
    stats = ServiceStats()
    task = PullTask(
        _FakeProvider([[]]), _FakeTester(passed_count=1), pool, stats, 10, 0.5,
        asyncio.Lock(), buffer_size=2,
    )
    worker = asyncio.create_task(task._run_worker())
    try:
        task._enqueue([make_ip(1)])
        task._enqueue([make_ip(2)])
        task._enqueue([make_ip(3)])
        task._enqueue([make_ip(4)])
        await task.join()
        records = await pool.all()
        assert sorted(r.ip for r in records) == ["10.0.0.3", "10.0.0.4"]
        assert stats.total_entered == 2
        assert task.drops == 2
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_worker_slow_does_not_block_pull_cadence(make_ip):
    """关键回归：测试 worker 被卡住时，拉取循环仍按节奏推进且队列有界。"""
    gate = asyncio.Event()

    class _SlowTester:
        async def test_many(self, ips):
            await gate.wait()  # 永不完成
            return ips

    provider = _FakeProvider([[make_ip(1), make_ip(2)]])
    pool = Level1Pool(max_size=1000)
    stats = ServiceStats()
    task = PullTask(provider, _SlowTester(), pool, stats, 10, 0.005, asyncio.Lock(), buffer_size=100)
    t = asyncio.create_task(task.run())
    try:
        await asyncio.sleep(0.1)
        assert provider.pull_calls >= 5
        assert stats.total_pulled >= 10
        assert task._queue.qsize() <= 100
    finally:
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        gate.set()


# ---------------------------------------------------------------------------
# 多 worker 并发测试管线
# ---------------------------------------------------------------------------


def test_worker_count_auto():
    """默认 worker 数 = max(1, test_concurrency // pull_count)。"""
    pool = Level1Pool(max_size=100)
    stats = ServiceStats()
    base = dict(provider=_FakeProvider([[]]), pool=pool, stats=stats,
                pull_count=10, pull_interval=1.0, pull_lock=asyncio.Lock())
    assert PullTask(
        **base, tester=tester_mod.Tester(timeout=1.0, concurrency=50)
    )._worker_count() == 5
    assert PullTask(
        **base, tester=tester_mod.Tester(timeout=1.0, concurrency=10)
    )._worker_count() == 1
    assert PullTask(
        **base, tester=tester_mod.Tester(timeout=1.0, concurrency=5)
    )._worker_count() == 1
    assert PullTask(**base, tester=_FakeTester(1))._worker_count() == 1


def test_worker_count_clamped():
    """显式 test_workers 超过安全上限时按 max(1, concurrency//count) 截断。"""
    pool = Level1Pool(max_size=100)
    stats = ServiceStats()
    base = dict(provider=_FakeProvider([[]]), pool=pool, stats=stats,
                pull_count=10, pull_interval=1.0, pull_lock=asyncio.Lock(),
                tester=tester_mod.Tester(timeout=1.0, concurrency=50))
    assert PullTask(**base, test_workers=10)._worker_count() == 5
    assert PullTask(**base, test_workers=2)._worker_count() == 2
    assert PullTask(**base, test_workers=0)._worker_count() == 5
    assert PullTask(**base, test_workers=-3)._worker_count() == 5


async def test_multiple_workers_parallel_and_capped(make_ip):
    """多 worker 并行处理批次，且全局探测并发不超过 test_concurrency。"""
    active = 0
    max_active = 0

    async def _track(ip):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.03)
        active -= 1
        return True, 1.0

    pool = Level1Pool(max_size=1000)
    stats = ServiceStats()
    tester = tester_mod.Tester(timeout=1.0, concurrency=20, test_fn=_track)
    task = PullTask(_FakeProvider([[]]), tester, pool, stats, 5, 0.5, asyncio.Lock())
    assert task._worker_count() == 4
    workers = [
        asyncio.create_task(task._run_worker())
        for _ in range(task._worker_count())
    ]
    try:
        for i in range(6):
            task._enqueue([make_ip(i * 10 + j) for j in range(5)])
        await task.join()
        assert max_active > 5      # 多批并行
        assert max_active <= 20    # 全局并发 ≤ test_concurrency
        assert stats.total_entered == 30
    finally:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


class _FiniteProvider:
    """先产出若干批，之后一直返回空列表（用于在 run() 运行中停止生产）。"""

    def __init__(self, batches):
        self._batches = list(batches)
        self.pull_calls = 0

    async def pull(self, count):
        if self.pull_calls < len(self._batches):
            batch = self._batches[self.pull_calls]
            self.pull_calls += 1
            return batch
        return []


class _CancelProvider:
    async def pull(self, count):
        raise asyncio.CancelledError()


async def test_pull_cancelled_during_pull_propagates(make_ip):
    """拉取协程内收到取消时透传 CancelledError 并清理 worker。"""
    pool = Level1Pool(max_size=100)
    stats = ServiceStats()
    task = PullTask(
        _CancelProvider(), _FakeTester(1), pool, stats, 10, 1.0, asyncio.Lock()
    )
    with pytest.raises(asyncio.CancelledError):
        await task.run()


async def test_no_drops_with_adequate_workers(make_ip):
    """测试吞吐足够时，队满丢批（drops）不发生，pulled == entered。"""
    class _SlowishTester:
        concurrency = 50

        async def test_many(self, ips):
            await asyncio.sleep(0.03)
            return ips

    pool = Level1Pool(max_size=5000)
    stats = ServiceStats()
    batches = [[make_ip(i * 5 + j) for j in range(5)] for i in range(8)]
    provider = _FiniteProvider(batches)
    task = PullTask(
        provider, _SlowishTester(), pool, stats, 5, 0.05, asyncio.Lock(),
        test_workers=5, buffer_size=20,
    )
    t = asyncio.create_task(task.run())
    await asyncio.sleep(0.5)
    await task.join()  # 供应商已停产出 → 队列排空
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert task.drops == 0
    assert stats.total_pulled == 40
    assert stats.total_entered == 40


async def test_drops_with_insufficient_workers(make_ip):
    """对照组：worker 不足时队满丢批发生（drops > 0）。"""
    class _SlowishTester:
        concurrency = 50

        async def test_many(self, ips):
            await asyncio.sleep(0.05)
            return ips

    pool = Level1Pool(max_size=5000)
    stats = ServiceStats()
    batches = [[make_ip(i * 5 + j) for j in range(5)] for i in range(8)]
    provider = _FiniteProvider(batches)
    task = PullTask(
        provider, _SlowishTester(), pool, stats, 5, 0.01, asyncio.Lock(),
        test_workers=1, buffer_size=2,
    )
    t = asyncio.create_task(task.run())
    await asyncio.sleep(0.3)
    await task.join()
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert task.drops > 0
    assert stats.total_entered < stats.total_pulled


# ---------------------------------------------------------------------------
# TTL 清扫
# ---------------------------------------------------------------------------


async def test_ttl_sweeper_removes_expired(make_ip):
    pool = Level1Pool(max_size=100)
    now = time.time()
    await pool.add(make_ip(1, ttl=5), now - 10)     # 过期
    await pool.add(make_ip(2, ttl=1000), now - 10)  # 未过期
    sleep_rec = _SleepRecorder(stop_after=2)
    sweeper = TtlSweeper(pool, 5.0, sleep_fn=sleep_rec)
    with pytest.raises(_StopLoop):
        await sweeper.run()
    remaining = await pool.all()
    assert len(remaining) == 1
    assert remaining[0].id == 1


async def test_ttl_sweeper_exception_isolation(make_ip):
    pool = Level1Pool(max_size=100)
    now = time.time()
    await pool.add(make_ip(1, ttl=5), now - 10)
    await pool.add(make_ip(2, ttl=1000), now - 10)

    calls = []
    original_sweep = pool.sweep_ttl

    async def _flaky_sweep(now_):
        calls.append(now_)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return await original_sweep(now_)

    pool.sweep_ttl = _flaky_sweep
    sleep_rec = _SleepRecorder(stop_after=3)
    sweeper = TtlSweeper(pool, 5.0, sleep_fn=sleep_rec)
    with pytest.raises(_StopLoop):
        await sweeper.run()
    assert len(calls) == 2
    remaining = await pool.all()
    assert len(remaining) == 1
    assert remaining[0].id == 1


async def test_ttl_sweeper_cancellation(make_ip):
    pool = Level1Pool(max_size=100)

    async def _never(duration):
        await asyncio.sleep(3600)

    sweeper = TtlSweeper(pool, 5.0, sleep_fn=_never)
    t = asyncio.create_task(sweeper.run())
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t


async def test_ttl_sweeper_cancelled_during_sweep(make_ip):
    pool = Level1Pool(max_size=100)

    async def _cancel_sweep(now_):
        raise asyncio.CancelledError()

    pool.sweep_ttl = _cancel_sweep
    sweeper = TtlSweeper(pool, 5.0, sleep_fn=_SleepRecorder(stop_after=None))
    with pytest.raises(asyncio.CancelledError):
        await sweeper.run()