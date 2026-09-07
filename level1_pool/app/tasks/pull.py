"""拉取循环：供应商拉取 + 并发测试管线（每个供应商实例一个，互不干扰）。

拉取与测试解耦：
- ``pull_lock`` 只保护 ``provider.pull``（供应商限频点），不再把测试串在拉取链上；
- 拉取按「tick 起点计时」调度，供应商响应快时恢复 ~``pull_interval`` 节奏
  （由 ``TickLoop`` 实现）；
- ``default_ttl`` 归一化：供应商未返回 ttl（None）且配置了 ``default_ttl``（>0）
  时，拉取后统一填充默认剩余秒数（供应商返回了 ttl 则不覆盖）；
- 拉到的批次进入有界队列，由多个测试 worker 并发消费（数量由
  ``resolve_worker_count`` 解析）：每批内部 ``test_many`` 再按 ``test_concurrency``
  并发探测代理可达性（仍只测代理、不测出口）；
- 队列满时丢弃最旧待测批次（有界内存，``drops`` 累计计数），被丢弃的 IP
  仍计入 ``total_pulled``；
- 多供应商场景下每个供应商实例化一个 PullTask（独立 ``pull_lock`` 限频、独立
  队列与测试 worker、独立 ``provider_stats`` 明细统计），共享同一个池；
- 单 tick / 单批次异常仅记日志，循环继续；支持 asyncio 取消优雅退出
  （由 ``BoundedTestPipeline``/``TickLoop`` 实现）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace

from ip_pool_common.concurrency import (
    BoundedTestPipeline,
    SleepFn,
    TickLoop,
    resolve_worker_count,
)

from ..core.pool import Level1Pool
from ..core.stats import ProviderStats, ServiceStats
from ..providers.base import BaseProvider
from ..testing.tester import Tester

logger = logging.getLogger(__name__)

__all__ = ["PullTask"]


class PullTask:
    """供应商拉取 + 并发测试管线（每个供应商实例一个，互不干扰）。"""

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
        name: str = "",
        provider_stats: ProviderStats | None = None,
        default_ttl: float | None = None,
    ):
        self._provider = provider
        self._tester = tester
        self._pool = pool
        self._stats = stats
        self._pull_count = pull_count
        self._pull_interval = pull_interval
        self._pull_lock = pull_lock
        self._sleep = sleep_fn
        self._name = name
        self._provider_stats = provider_stats
        self._default_ttl = default_ttl if default_ttl and default_ttl > 0 else None
        self._pipeline = BoundedTestPipeline(
            self._process_batch,
            worker_count=resolve_worker_count(
                test_workers,
                getattr(tester, "concurrency", 0) or 0,
                pull_count,
            ),
            buffer_size=buffer_size,
            on_error=self._on_batch_error,
            on_drop=self._on_drop,
            name=name or "-",
        )

    # ------------------------------------------------------------------
    # 兼容旧内部接口（供既有测试直接驱动管线；新代码请使用 pipeline 属性）
    # ------------------------------------------------------------------

    @property
    def _queue(self) -> asyncio.Queue:
        return self._pipeline.queue

    @property
    def _pipeline_(self) -> BoundedTestPipeline:
        return self._pipeline

    def _worker_count(self) -> int:
        return self._pipeline.worker_count

    def _enqueue(self, raw: list) -> None:
        self._pipeline.enqueue(raw)

    async def _run_worker(self) -> None:
        await self._pipeline.run_worker()

    # ------------------------------------------------------------------

    @property
    def drops(self) -> int:
        """因队满被丢弃的待测批次累计数。"""
        return self._pipeline.drops

    async def run(self) -> None:
        """拉取主循环；同时启动并持有全部测试 worker 的生命周期。"""
        driver = TickLoop(
            self._pull_tick,
            interval=self._pull_interval,
            on_error=self._on_pull_error,
            sleep_fn=self._sleep,
        )
        await self._pipeline.run_supervised(driver.run())

    async def join(self) -> None:
        """等待全部已入队批次被测试完成（供测试/排空使用）。"""
        await self._pipeline.join()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    async def _pull_tick(self) -> None:
        """单个拉取周期：限频拉取 → ttl 归一化 → 统计 → 入队。"""
        async with self._pull_lock:
            raw = await self._provider.pull(self._pull_count)
        if self._default_ttl is not None:
            # 供应商未返回 ttl 的项填充默认剩余秒数（已返回的不覆盖）
            raw = [
                ip if ip.ttl is not None else replace(ip, ttl=self._default_ttl)
                for ip in raw
            ]
        self._stats.total_pulled += len(raw)
        if self._provider_stats is not None:
            self._provider_stats.total_pulled += len(raw)
        if raw:
            self._pipeline.enqueue(raw)

    async def _process_batch(self, raw: list) -> None:
        """测试 worker 批处理：并发可达性测试，通过的入池并累计 total_entered。"""
        passed = await self._tester.test_many(raw)
        for ip in passed:
            await self._pool.add(ip, time.time())
        self._stats.total_entered += len(passed)
        if self._provider_stats is not None:
            self._provider_stats.total_entered += len(passed)

    def _on_pull_error(self, exc: BaseException) -> None:
        logger.error(
            "pull tick failed (provider=%s)", self._name or "-", exc_info=exc
        )
        self._stats.pull_failures += 1
        if self._provider_stats is not None:
            self._provider_stats.pull_failures += 1

    def _on_batch_error(self, exc: BaseException) -> None:
        logger.error(
            "test batch failed (provider=%s)", self._name or "-", exc_info=exc
        )
        self._stats.test_failures += 1
        if self._provider_stats is not None:
            self._provider_stats.test_failures += 1

    def _on_drop(self) -> None:
        self._stats.drops += 1
        if self._provider_stats is not None:
            self._provider_stats.drops += 1
