"""周期复验任务：对池内全部 IP 做代理可达性复验，删除不通过项。"""
from __future__ import annotations

import logging

from ip_pool_common.concurrency import SleepFn, run_periodic

from ..core.pool import Level2Pool
from ..core.stats import ServiceStats
from ..testing.tester import Tester

logger = logging.getLogger(__name__)

__all__ = ["RevalidateTask"]


class RevalidateTask:
    """每 ``interval`` 秒对池内全部 IP 做代理可达性复验，删除不通过项。"""

    def __init__(
        self,
        pool: Level2Pool,
        tester: Tester,
        interval: float = 60.0,
        stats: ServiceStats | None = None,
        sleep_fn: SleepFn | None = None,
    ) -> None:
        self._pool = pool
        self._tester = tester
        self._interval = interval
        self._stats = stats
        self._sleep = sleep_fn

    async def run(self) -> None:
        await run_periodic(
            self._tick,
            interval=self._interval,
            on_error=self._on_error,
            sleep_fn=self._sleep,
        )

    async def _tick(self) -> None:
        records = self._pool.all()
        alive = await self._tester.revalidate(records)
        alive_ids = {rec.id for rec in alive}
        for rec in records:
            if rec.id not in alive_ids:
                await self._pool.remove(rec.id)

    def _on_error(self, exc: BaseException) -> None:
        logger.error("revalidate tick failed", exc_info=exc)
        if self._stats is not None:
            self._stats.revalidate_failures += 1
