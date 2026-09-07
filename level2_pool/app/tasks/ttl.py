"""TTL 清扫任务：复用 ``ip_pool_common.concurrency.run_periodic``。"""
from __future__ import annotations

import logging
import time

from ip_pool_common.concurrency import SleepFn, run_periodic

from ..core.pool import Level2Pool
from ..core.stats import ServiceStats

logger = logging.getLogger(__name__)

__all__ = ["TtlSweeper"]


class TtlSweeper:
    """每 ``interval`` 秒清扫一次 TTL 过期项。"""

    def __init__(
        self,
        pool: Level2Pool,
        interval: float = 5.0,
        stats: ServiceStats | None = None,
        sleep_fn: SleepFn | None = None,
    ) -> None:
        self._pool = pool
        self._interval = interval
        self._stats = stats
        self._sleep = sleep_fn

    async def run(self) -> None:
        await run_periodic(
            self._sweep,
            interval=self._interval,
            on_error=self._on_error,
            sleep_fn=self._sleep,
        )

    async def _sweep(self) -> None:
        await self._pool.sweep_ttl(time.time())

    def _on_error(self, exc: BaseException) -> None:
        logger.error("ttl sweep failed", exc_info=exc)
        if self._stats is not None:
            self._stats.ttl_sweep_failures += 1
