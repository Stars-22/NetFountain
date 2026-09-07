"""并发基础设施：周期任务、tick 循环、有界队列批处理管线。

供 level1_pool（PullTask/TtlSweeper）与 level2_pool（SyncTask/RevalidateTask/
TtlSweeper）复用，消除各自重复的「队列 + worker + drop-oldest」与
「sleep → 执行 → 异常兜底」样板。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "BoundedTestPipeline",
    "SleepFn",
    "TickLoop",
    "resolve_worker_count",
    "run_periodic",
]

SleepFn = Callable[[float], Awaitable[None]]


def resolve_worker_count(
    explicit: int | None, concurrency: int, expected_batch: int
) -> int:
    """解析测试 worker 数量。

    - 默认 ``max(1, concurrency // expected_batch)``（concurrency 无效时为 1）；
    - 显式 ``explicit`` 生效时取 ``max(1, min(explicit, safe))``（超上限截断）。
    """
    safe = max(1, concurrency // expected_batch) if concurrency > 0 else 1
    if explicit is not None and explicit > 0:
        return max(1, min(explicit, safe))
    return safe


class TickLoop:
    """按 tick 起点计时的周期循环：慢 tick 不拖累节奏，单次异常不中断循环。

    - ``tick``：单个周期要执行的协程函数（拉取/同步一次）；
    - ``on_error``：tick 异常回调（如累计失败计数）；None 时仅记日志；
    - 支持 asyncio 取消优雅退出。
    """

    def __init__(
        self,
        tick: Callable[[], Awaitable[None]],
        *,
        interval: float,
        on_error: Callable[[BaseException], None] | None = None,
        sleep_fn: SleepFn | None = None,
    ) -> None:
        self._tick = tick
        self._interval = interval
        self._on_error = on_error
        self._sleep = sleep_fn or asyncio.sleep

    async def run(self) -> None:
        while True:
            start = time.monotonic()
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._on_error is not None:
                    self._on_error(exc)
                else:
                    logger.exception("tick failed")
            elapsed = time.monotonic() - start
            await self._sleep(max(0.0, self._interval - elapsed))


async def run_periodic(
    action: Callable[[], Awaitable[None]],
    *,
    interval: float,
    on_error: Callable[[BaseException], None] | None = None,
    sleep_fn: SleepFn | None = None,
) -> None:
    """先休眠后执行的周期任务（TTL 清扫、复验等）；单次异常不中断循环。"""
    sleep = sleep_fn or asyncio.sleep
    while True:
        await sleep(interval)
        try:
            await action()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            else:
                logger.exception("periodic action failed")


class BoundedTestPipeline:
    """有界队列 + 固定 worker 池的批处理管线（拉取与处理解耦）。

    - 生产端 ``enqueue``：队满时丢弃最旧批次保证内存有界，累计 ``drops``
      并回调 ``on_drop``；
    - 消费端：``worker_count`` 个 worker 并发执行 ``handle_batch(batch)``，
      批次异常回调 ``on_error``（None 时记日志），不影响其它批次；
    - ``run_supervised``：启动全部 worker 运行 driver 协程，driver 结束或
      取消后回收 worker。
    """

    def __init__(
        self,
        handle_batch: Callable[[list], Awaitable[None]],
        *,
        worker_count: int,
        buffer_size: int = 20,
        on_error: Callable[[BaseException], None] | None = None,
        on_drop: Callable[[], None] | None = None,
        name: str = "-",
    ) -> None:
        self._handle_batch = handle_batch
        self.worker_count = max(1, worker_count)
        self._on_error = on_error
        self._on_drop = on_drop
        self._name = name or "-"
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=buffer_size)
        self._drops = 0

    @property
    def queue(self) -> asyncio.Queue:
        """内部待处理队列（暴露供观测 qsize 等运行状态）。"""
        return self._queue

    @property
    def drops(self) -> int:
        """因队满被丢弃的待测批次累计数。"""
        return self._drops

    def enqueue(self, batch: list) -> None:
        """入队待测批次；队满时丢弃最旧批次，保证内存有界。"""
        try:
            self._queue.put_nowait(batch)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                return
            self._drops += 1
            if self._on_drop is not None:
                self._on_drop()
            logger.warning(
                "test queue full, dropped oldest pending batch (name=%s, drops=%d)",
                self._name,
                self._drops,
            )
            try:
                self._queue.put_nowait(batch)
            except asyncio.QueueFull:
                pass

    async def join(self) -> None:
        """等待全部已入队批次被处理完成（供测试/排空使用）。"""
        await self._queue.join()

    async def run_supervised(self, driver: Awaitable) -> None:
        """启动全部 worker 并运行 ``driver``；结束后回收 worker。"""
        workers = [
            asyncio.create_task(self.run_worker())
            for _ in range(self.worker_count)
        ]
        try:
            await driver
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def run_worker(self) -> None:
        """批处理 worker：消费队列直到被取消。"""
        while True:
            batch = await self._queue.get()
            try:
                await self._handle_batch(batch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._on_error is not None:
                    self._on_error(exc)
                else:
                    logger.exception("test batch failed (name=%s)", self._name)
            finally:
                self._queue.task_done()
