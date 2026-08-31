"""后台任务：增量同步（SyncTask）、周期复验（RevalidateTask）、TTL 清扫（TtlSweeper）。

- 复验每 ``revalidate_interval``(60s) 对池内全部 IP 做代理可达性测试，
  删除不通过项（含租赁中的，按策划书语义）；
- TTL 清扫每 ``ttl_sweep_interval``(5s) 删除过期项；
- 单个 tick 的任何异常仅记日志，循环继续；支持 asyncio 取消优雅退出。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from .syncer import SleepFn, SyncTask  # noqa: F401  # re-export

logger = logging.getLogger(__name__)


class RevalidateTask:
    """每 ``interval`` 秒对池内全部 IP 做代理可达性复验，删除不通过项。"""

    def __init__(
        self,
        pool: object,
        tester: object,
        interval: float = 60.0,
        stats: object | None = None,
        sleep_fn: SleepFn | None = None,
    ) -> None:
        self._pool = pool
        self._tester = tester
        self._interval = interval
        self._stats = stats
        self._sleep = sleep_fn or asyncio.sleep

    async def run(self) -> None:
        while True:
            await self._sleep(self._interval)
            try:
                records = self._pool.all()
                alive = await self._tester.revalidate(records)
                alive_ids = {rec.id for rec in alive}
                for rec in records:
                    if rec.id not in alive_ids:
                        await self._pool.remove(rec.id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("revalidate tick failed")
                if self._stats is not None:
                    self._stats.revalidate_failures += 1


class TtlSweeper:
    """每 ``interval`` 秒清扫一次 TTL 过期项。"""

    def __init__(
        self,
        pool: object,
        interval: float = 5.0,
        stats: object | None = None,
        sleep_fn: SleepFn | None = None,
    ) -> None:
        self._pool = pool
        self._interval = interval
        self._stats = stats
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
                if self._stats is not None:
                    self._stats.ttl_sweep_failures += 1