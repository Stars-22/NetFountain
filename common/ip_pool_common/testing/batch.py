"""批量并发测试执行器：信号量限流 + 异常兜底 + 可选失败原因收集。

- ``batch_test``：既有语义——仅返回 ok 项，保持原顺序；
- ``run_batch_detailed``：扩展语义——返回 ``(item, ok, latency, reason)`` 全量
  结果（含失败项与原因键），供需要汇总失败构成的调用方（如二级池批次日志）复用。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from ..errors import classify_test_error

__all__ = ["batch_test", "run_batch_detailed"]

#: 单项测试函数：返回 ``(ok, latency)`` 或 ``(ok, latency, reason)``
TestFn = Callable[[Any], Awaitable[tuple]]


async def run_batch_detailed(
    items: Sequence[Any],
    test_fn: TestFn,
    concurrency: int = 20,
) -> list[tuple[Any, bool, float, str | None]]:
    """信号量并发批量执行 ``test_fn``，返回与输入等长且保序的全量结果。

    - 并发不超过 ``concurrency``（``<1`` 视为 1）；
    - ``test_fn`` 返回 ``(ok, latency)`` 时 reason 为 None；
      返回 ``(ok, latency, reason)`` 时透传 reason；
    - ``test_fn`` 抛异常按 ``(False, 0.0, 原因键)`` 处理，不影响其它项。
    """
    if concurrency < 1:
        concurrency = 1
    sem = asyncio.Semaphore(concurrency)

    async def _run(item: Any) -> tuple[Any, bool, float, str | None]:
        async with sem:
            try:
                res = await test_fn(item)
                if len(res) == 3:
                    ok, latency, reason = res
                else:
                    ok, latency = res
                    reason = None
            except Exception as exc:
                ok, latency, reason = False, 0.0, classify_test_error(exc)
        return item, ok, latency, reason

    return await asyncio.gather(*(_run(item) for item in items))


async def batch_test(
    items: Sequence[Any],
    test_fn: Callable[[Any], Awaitable[tuple[bool, float]]],
    concurrency: int = 20,
) -> list[Any]:
    """信号量并发批量测试：并发不超过 ``concurrency``，仅返回 ok=True 的项，保持原顺序。"""
    results = await run_batch_detailed(items, test_fn, concurrency)
    return [item for item, ok, _, _ in results if ok]
