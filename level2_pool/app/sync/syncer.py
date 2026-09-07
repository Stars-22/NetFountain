"""增量同步任务（含空响应重置）：接入通用有界测试管线。

- 维护 ``last_synced_id`` 水位线，增量返回空时按一级池 ``max_id`` 判定：
  仅当水位线已越过一级池最大 id（``last_synced_id > max_id``，即一级池重启/
  换代）才全量重拉并重置水位线；水位线等于最大 id 视为一级池暂无新 IP 而跳过。
  **绝对不移除池内现存记录**（空闲与租赁均保留），旧记录靠复验与 TTL 自然淘汰；
- 拉取与测试解耦：拉取阶段只拉取并推进水位线（按 tick 起点计时，不受测试耗时
  拖慢，由 ``TickLoop`` 实现），拉到的批次进入有界队列，由多个测试 worker
  并发消费（数量由 ``resolve_worker_count`` 解析）：每批内部 ``site_filter``
  再按 ``test_concurrency`` 并发探测站点；
- 队列满时丢弃最旧待测批次（有界内存，``drops`` 累计计数），被丢弃的 IP
  仍计入 ``total_pulled``；
- 单 tick / 单批次异常仅记日志，循环继续；支持 asyncio 取消优雅退出。
"""
from __future__ import annotations

import asyncio
import logging

from ip_pool_common.concurrency import (
    BoundedTestPipeline,
    SleepFn,
    TickLoop,
    resolve_worker_count,
)

from ..core.pool import Level2Pool
from ..core.stats import ServiceStats
from ..testing.tester import Tester
from .client import Level1SyncClient

logger = logging.getLogger(__name__)

__all__ = ["SyncTask"]


class SyncTask:
    """增量同步任务：拉取阶段 + 多 worker 并发测试管线。

    拉取阶段只拉取并入队（推进水位线），站点测试由多个 worker 并行
    消费队列完成。
    """

    _EXPECTED_BATCH = 10  #: 自动 worker 数公式的期望批大小（对应一级池默认 pull_count）

    def __init__(
        self,
        client: Level1SyncClient,
        tester: Tester,
        pool: Level2Pool,
        stats: ServiceStats,
        interval: float = 3.0,
        sleep_fn: SleepFn | None = None,
        buffer_size: int = 20,
        test_workers: int | None = None,
    ) -> None:
        self._client = client
        self._tester = tester
        self._pool = pool
        self._stats = stats
        self._interval = interval
        self._sleep = sleep_fn or asyncio.sleep
        self._pipeline = BoundedTestPipeline(
            self._process_batch,
            worker_count=resolve_worker_count(
                test_workers,
                getattr(tester, "concurrency", 0) or 0,
                SyncTask._EXPECTED_BATCH,
            ),
            buffer_size=buffer_size,
            on_error=self._on_batch_error,
            on_drop=self._on_drop,
        )
        self.last_synced_id: int | None = None

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

    def _enqueue(self, batch: list) -> None:
        self._pipeline.enqueue(batch)

    async def _run_worker(self) -> None:
        await self._pipeline.run_worker()

    # ------------------------------------------------------------------

    @property
    def drops(self) -> int:
        """因队满被丢弃的待测批次累计数。"""
        return self._pipeline.drops

    async def join(self) -> None:
        """等待全部已入队批次被测试完成（供测试/排空使用）。"""
        await self._pipeline.join()

    async def run(self) -> None:
        """拉取主循环；同时启动并持有全部测试 worker 的生命周期。

        按 tick 起点计时维护 ``interval`` 节奏，慢测试不阻塞拉取；单 tick
        异常仅记日志，不影响池内现有记录；支持取消。
        """
        driver = TickLoop(
            self._sync_once,
            interval=self._interval,
            on_error=self._on_sync_error,
            sleep_fn=self._sleep,
        )
        await self._pipeline.run_supervised(driver.run())

    async def _sync_once(self) -> None:
        """拉取阶段：拉取 → 推进水位线 → 入队待测批次；不做测试。

        增量返回空时，依据一级池 ``max_id``（响应顶层字段）区分两种情形：
        - ``last_synced_id > max_id``：一级池 id 空间已重置（重启/换代），
          全量重拉并重置水位线（**绝对不移除池内现存记录**，空闲与租赁均保留，
          旧记录靠复验与 TTL 自然淘汰）；
        - ``last_synced_id == max_id``：一级池暂无新 IP，跳过本次（不做全量提取）；
        - ``max_id`` 缺失（旧版/空池）：回退全量重拉，保持向后兼容。
        """
        if self.last_synced_id is None:
            batch = await self._client.fetch_all()
        else:
            batch, max_id = await self._client.fetch_after(self.last_synced_id)
            if not batch and (max_id is None or self.last_synced_id > max_id):
                batch = await self._client.fetch_all()
        if batch:
            self.last_synced_id = max(r.id for r in batch)
            if self._stats is not None:
                self._stats.last_synced_id = self.last_synced_id
        if self._stats is not None:
            self._stats.total_pulled += len(batch)
        if batch:
            self._pipeline.enqueue(batch)

    async def _process_batch(self, batch: list) -> None:
        """测试 worker 批处理：站点过滤，通过的入池并累计 total_entered。"""
        passed = await self._tester.site_filter(batch)
        for rec in passed:
            await self._pool.upsert(rec)
        if self._stats is not None:
            self._stats.total_entered += len(passed)

    def _on_sync_error(self, exc: BaseException) -> None:
        logger.error("sync tick failed", exc_info=exc)
        if self._stats is not None:
            self._stats.sync_failures += 1

    def _on_batch_error(self, exc: BaseException) -> None:
        logger.error("sync test batch failed", exc_info=exc)
        if self._stats is not None:
            self._stats.test_failures += 1

    def _on_drop(self) -> None:
        if self._stats is not None:
            self._stats.drops += 1
