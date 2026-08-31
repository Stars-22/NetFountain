"""站点测试与周期复验封装。

- ``site_filter``：经代理真实访问目标站点（唯一出口测试），
  ok 且 ``latency < threshold_ms`` 才保留，构造 ``Level2Record``；
- ``revalidate``：仅做代理可达性测试（``proxy_reachability_test``，不测出口），
  返回仍存活项。
- 每批测试结束后输出汇总日志：``total / ok / fail`` 及各失败原因计数
  （如 ``timeout*10``），供排查失败构成。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from collections import Counter

from ip_pool_common.models import IpRecord, Level2Record
from ip_pool_common.testing import (
    classify_test_error,
    proxy_reachability_test_detailed,
    site_test_detailed,
)

SiteTestFn = Callable[[IpRecord], Awaitable[tuple[bool, float]]]
RevalidateFn = Callable[[Level2Record], Awaitable[tuple[bool, float]]]

logger = logging.getLogger(__name__)


def _format_reasons(reasons: dict[str, int]) -> str:
    """将原因计数格式化为 ``timeout*10, connect*3`` 形式。"""
    return ", ".join(f"{key}*{count}" for key, count in sorted(reasons.items()))


class Tester:
    """站点连通测试器；可通过 ``site_fn`` / ``revalidate_fn`` 注入替换验证策略。"""

    def __init__(
        self,
        target_url: str,
        threshold_ms: int = 2000,
        connect_timeout: float = 3.0,
        concurrency: int = 20,
        site_fn: SiteTestFn | None = None,
        revalidate_fn: RevalidateFn | None = None,
    ) -> None:
        self.target_url = target_url
        self.threshold_ms = threshold_ms
        self.connect_timeout = connect_timeout
        self.concurrency = concurrency
        self._site_fn = site_fn
        self._revalidate_fn = revalidate_fn

    async def _site(self, rec: IpRecord) -> tuple[bool, float, str | None]:
        if self._site_fn is not None:
            res = self._site_fn(rec)
            ok, latency = (
                await res if inspect.isawaitable(res) else res
            )
            return ok, latency, None
        return await site_test_detailed(
            rec.proxy_url, self.target_url, timeout=self.connect_timeout
        )

    async def _revalidate(self, rec: Level2Record) -> tuple[bool, float, str | None]:
        if self._revalidate_fn is not None:
            res = self._revalidate_fn(rec)
            ok, latency = (
                await res if inspect.isawaitable(res) else res
            )
            return ok, latency, None
        return await proxy_reachability_test_detailed(
            rec.proxy_url, timeout=self.connect_timeout
        )

    async def site_filter(self, records: list[IpRecord]) -> list[Level2Record]:
        """逐个 site_test 目标站点；ok 且 latency < threshold_ms 才保留并构造 Level2Record。

        测试结束后输出批次汇总日志：``total / ok / fail`` 及失败原因计数。
        """
        if not records:
            return []
        sem = asyncio.Semaphore(self.concurrency)

        async def _run(rec: IpRecord) -> tuple[bool, float, str | None]:
            async with sem:
                try:
                    return await self._site(rec)
                except Exception as exc:
                    return False, 0.0, classify_test_error(exc)

        results = await asyncio.gather(*(_run(rec) for rec in records))
        now = time.time()
        ok_count = 0
        reasons: Counter[str] = Counter()
        result: list[Level2Record] = []
        for rec, (ok, latency, reason) in zip(records, results):
            if ok and latency < self.threshold_ms:
                ok_count += 1
                result.append(
                    Level2Record(
                        id=-1,
                        ip=rec.ip,
                        port=rec.port,
                        protocol=rec.protocol,
                        proxy_url=rec.proxy_url,
                        region=rec.region,
                        ttl=rec.ttl,
                        latency_ms=latency,
                        leased=False,
                        leased_at=None,
                        created_at=now,
                        last_verified_at=now,
                    )
                )
            else:
                if ok and latency >= self.threshold_ms:
                    reasons["slow"] += 1
                else:
                    reasons[reason or "rejected"] += 1
        self._log_batch("site test batch", len(records), ok_count, reasons)
        return result

    async def revalidate(self, records: list[Level2Record]) -> list[Level2Record]:
        """逐个代理可达性测试（不测出口），返回仍存活项，保持原顺序。

        测试结束后输出批次汇总日志：``total / ok / fail`` 及失败原因计数。
        """
        if not records:
            return []
        sem = asyncio.Semaphore(self.concurrency)

        async def _run(rec: Level2Record) -> tuple[Level2Record, bool, str | None]:
            async with sem:
                try:
                    ok, _, reason = await self._revalidate(rec)
                except Exception as exc:
                    ok, reason = False, classify_test_error(exc)
            return rec, ok, reason

        results = await asyncio.gather(*(_run(rec) for rec in records))
        alive: list[Level2Record] = []
        reasons: Counter[str] = Counter()
        for rec, ok, reason in results:
            if ok:
                alive.append(rec)
            else:
                reasons[reason or "rejected"] += 1
        self._log_batch("revalidate batch", len(records), len(alive), reasons)
        return alive

    @staticmethod
    def _log_batch(
        label: str, total: int, ok_count: int, reasons: Counter[str]
    ) -> None:
        """输出批次汇总日志：``total=X ok=Y fail=Z (timeout*10, ...)``。"""
        fail = total - ok_count
        detail = f"total={total} ok={ok_count} fail={fail}"
        if reasons:
            detail += f" ({_format_reasons(reasons)})"
        logger.info("%s: %s", label, detail)