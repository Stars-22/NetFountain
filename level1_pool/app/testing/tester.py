"""批量代理可达性测试封装。

硬性语义：只测「代理可达性」（``proxy_reachability_test``），
即仅验证能否与代理建立代理协议会话，**绝不测出口**。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from ip_pool_common.models import ProviderIp, build_proxy_url
from ip_pool_common.testing import batch_test, proxy_reachability_test

TestFn = Callable[[ProviderIp], Awaitable[tuple[bool, float]]]

__all__ = ["Tester"]


class Tester:
    """并发代理可达性测试器；可通过 ``test_fn`` 注入替换验证策略。"""

    def __init__(
        self,
        timeout: float = 3.0,
        concurrency: int = 10,
        test_fn: TestFn | None = None,
    ):
        self.timeout = timeout
        self.concurrency = concurrency
        self._test_fn = test_fn

    async def _run(self, ip: ProviderIp) -> tuple[bool, float]:
        if self._test_fn is not None:
            return await self._test_fn(ip)
        return await proxy_reachability_test(
            build_proxy_url(ip.ip, ip.port, ip.protocol), timeout=self.timeout
        )

    async def test_many(self, ips: list[ProviderIp]) -> list[ProviderIp]:
        """批量并发测试，仅返回通过的项，保持原顺序。"""
        if not ips:
            return []
        return await batch_test(ips, self._run, concurrency=self.concurrency)
