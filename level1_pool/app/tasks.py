"""后台任务：拉取循环（PullTask）+ 并发测试管线（run_worker）与 TTL 清扫（TtlSweeper）。

拉取与测试解耦：
- ``pull_lock`` 只保护 ``provider.pull``（供应商限频点），不再把测试串在拉取链上；
- 拉取按「tick 起点计时」调度，供应商响应快时恢复 ~``pull_interval`` 节奏；
- 拉到的批次进入有界队列，由多个测试 worker（默认
  ``max(1, test_concurrency // pull_count)``）并发消费：每批内部 ``test_many``
  再按 ``test_concurrency`` 并发探测代理可达性（仍只测代理、不测出口），
  总并发不超过 ``test_concurrency``；
- 队列满时丢弃最旧待测批次（有界内存，``drops`` 累计计数），被丢弃的 IP
  仍计入 ``total_pulled``；
- 单 tick / 单批次异常仅记日志，循环继续；支持 asyncio 取消优雅退出。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from .pool import Level1Pool, ServiceStats
from .provider import BaseProvider
from .tester import Tester

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], Awaitable[None]]


class PullTask:
    """供应商拉取 + 并发测试管线。"""

    def __init__(
        self,
        provider: BaseProvider,
        tester: Tester,
        pool: Level1Pool,
        stats: ServiceStats,
        pull_count: int,
        pull_interval: float,
        pull_lock: asyncio.Lock,
        buffer_size: int = 20,
        test_workers: int | None = None,
        sleep_fn: SleepFn | None = None,
    ):
        self._provider = provider
        self._tester = tester
        self._pool = pool
        self._stats = stats
        self._pull_count = pull_count
        self._pull_interval = pull_interval
        self._pull_lock = pull_lock
        self._buffer_size = buffer_size
        self._test_workers = test_workers
        self._sleep = sleep_fn or asyncio.sleep
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=buffer_size)
        self._drops = 0

    def _worker_count(self) -> int:
        """解析测试 worker 数量：显式 ``test_workers`` 超上限时按上限截断。"""
        concurrency = getattr(self._tester, "concurrency", 0) or 0
        safe = max(1, concurrency // self._pull_count) if concurrency > 0 else 1
        if self._test_workers is not None and self._test_workers > 0:
            return max(1, min(self._test_workers, safe))
        return safe

    @property
    def drops(self) -> int:
        """因队满被丢弃的待测批次累计数。"""
        return self._drops

    async def run(self) -> None:
        """拉取主循环；同时启动并持有全部测试 worker 的生命周期。"""
        workers = [
            asyncio.create_task(self._run_worker())
            for _ in range(self._worker_count())
        ]
        try:
            while True:
                start = time.monotonic()
                try:
                    async with self._pull_lock:
                        raw = await self._provider.pull(self._pull_count)
                    self._stats.total_pulled += len(raw)
                    if raw:
                        self._enqueue(raw)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("pull tick failed")
                elapsed = time.monotonic() - start
                await self._sleep(max(0.0, self._pull_interval - elapsed))
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def _run_worker(self) -> None:
        """测试 worker：消费待测批次，通过的入池并累计 total_entered。"""
        while True:
            raw = await self._queue.get()
            try:
                passed = await self._tester.test_many(raw)
                for ip in passed:
                    await self._pool.add(ip, time.time())
                self._stats.total_entered += len(passed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("test batch failed")
            finally:
                self._queue.task_done()

    def _enqueue(self, raw: list) -> None:
        """入队待测批次；队满时丢弃最旧批次，保证内存有界并累计 drops。"""
        try:
            self._queue.put_nowait(raw)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                return
            self._drops += 1
            logger.warning(
                "test queue full, dropped oldest pending batch (drops=%d)",
                self._drops,
            )
            try:
                self._queue.put_nowait(raw)
            except asyncio.QueueFull:
                pass

    async def join(self) -> None:
        """等待全部已入队批次被测试完成（供测试/排空使用）。"""
        await self._queue.join()


class TtlSweeper:
    """每 ``interval`` 秒清扫一次 TTL 过期项。"""

    def __init__(
        self,
        pool: Level1Pool,
        interval: float,
        sleep_fn: SleepFn | None = None,
    ):
        self._pool = pool
        self._interval = interval
        self._sleep = sleep_fn or asyncio.sleep

    async def run(self) -> None:
        while True:
            await self._sleep(self._interval)
            try:
                await self._pool.sweep_ttl(time.time())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ttl sweep failed")