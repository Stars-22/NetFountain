"""增量同步（含空响应重置）与拉取-测试解耦的多 worker 测试管线。

- ``Level1SyncClient``：消费一级池 HTTP 契约
  ``GET /api/v1/ips``（全量）、``GET /api/v1/ips/after/{id}``（按 id 增量）；
- ``SyncTask``：每 ``interval`` 秒同步一次；增量返回空时判定一级池重启/换代，
  全量重拉并重置水位线，**绝对不移除池内现存记录**（空闲与租赁均保留），
  旧记录靠复验与 TTL 自然淘汰；
- 拉取与测试解耦：拉取阶段只拉取并推进水位线（按 tick 起点计时，不受测试耗时
  拖慢），拉到的批次进入有界队列，由多个测试 worker（默认
  ``max(1, test_concurrency // 10)``）并发消费：每批内部 ``site_filter``
  再按 ``test_concurrency`` 并发探测站点，总并发不超过 ``test_concurrency`` 的量级；
- 队列满时丢弃最旧待测批次（有界内存，``drops`` 累计计数），被丢弃的 IP
  仍计入 ``total_pulled``；
- 单 tick / 单批次异常仅记日志，循环继续；支持 asyncio 取消优雅退出。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import aiohttp

from ip_pool_common.models import IpRecord, Protocol, build_proxy_url

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], Awaitable[None]]


class Level1SyncClient:
    """一级池 HTTP 客户端：全量拉取与按 id 增量拉取。"""

    def __init__(
        self,
        base_url: str,
        session: aiohttp.ClientSession,
        timeout: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._timeout = timeout

    async def _get_items(self, url: str) -> list[dict]:
        async with self._session.get(
            url, timeout=aiohttp.ClientTimeout(total=self._timeout)
        ) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=resp.status,
                    message=f"level1 responded HTTP {resp.status}",
                )
            payload = await resp.json()
        if not isinstance(payload, dict):
            raise ValueError(f"level1 response is not an object: {url}")
        data = payload.get("data")
        if data is None:
            return []
        if not isinstance(data, list):
            raise ValueError(f"level1 data is not a list: {url}")
        return data

    @staticmethod
    def _to_ip_record(item: dict) -> IpRecord:
        protocol = Protocol(str(item["protocol"]).lower())
        ip = item["ip"]
        port = int(item["port"])
        return IpRecord(
            id=int(item["id"]),
            ip=ip,
            port=port,
            protocol=protocol,
            proxy_url=item.get("proxy_url") or build_proxy_url(ip, port, protocol),
            region=item.get("region"),
            ttl=item.get("ttl"),
            created_at=item.get("created_at") or 0.0,
            last_verified_at=item.get("last_verified_at") or 0.0,
        )

    async def fetch_all(self) -> list[IpRecord]:
        """GET /api/v1/ips：全量拉取，解析 data 为 IpRecord。"""
        items = await self._get_items(f"{self._base_url}/api/v1/ips")
        return [self._to_ip_record(item) for item in items]

    async def fetch_after(self, id_: int) -> list[IpRecord]:
        """GET /api/v1/ips/after/{id}：增量拉取；data 为空返回 []。"""
        items = await self._get_items(f"{self._base_url}/api/v1/ips/after/{id_}")
        return [self._to_ip_record(item) for item in items]


class SyncTask:
    """增量同步任务：拉取阶段 + 多 worker 并发测试管线。

    维护 ``last_synced_id`` 水位线，空响应触发全量重拉。拉取阶段只拉取并
    入队（推进水位线），站点测试由多个 worker 并行消费队列完成。
    """

    _EXPECTED_BATCH = 10  #: 自动 worker 数公式的期望批大小（对应一级池默认 pull_count）

    def __init__(
        self,
        client: Level1SyncClient,
        tester: object,
        pool: object,
        stats: object,
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
        self._test_workers = test_workers
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=buffer_size)
        self._drops = 0
        self.last_synced_id: int | None = None

    def _worker_count(self) -> int:
        """解析测试 worker 数量：显式 ``test_workers`` 超上限时按上限截断。"""
        concurrency = getattr(self._tester, "concurrency", 0) or 0
        safe = (
            max(1, concurrency // SyncTask._EXPECTED_BATCH)
            if concurrency > 0
            else 1
        )
        if self._test_workers is not None and self._test_workers > 0:
            return max(1, min(self._test_workers, safe))
        return safe

    @property
    def drops(self) -> int:
        """因队满被丢弃的待测批次累计数。"""
        return self._drops

    async def _sync_once(self) -> None:
        """拉取阶段：拉取 → 推进水位线 → 入队待测批次；不做测试。"""
        if self.last_synced_id is None:
            batch = await self._client.fetch_all()
        else:
            batch = await self._client.fetch_after(self.last_synced_id)
            if not batch:
                batch = await self._client.fetch_all()
        if batch:
            self.last_synced_id = max(r.id for r in batch)
            if self._stats is not None:
                self._stats.last_synced_id = self.last_synced_id
        if self._stats is not None:
            self._stats.total_pulled += len(batch)
        if batch:
            self._enqueue(batch)

    async def _run_worker(self) -> None:
        """测试 worker：消费待测批次，通过站点测试的入池并累计 total_entered。"""
        while True:
            batch = await self._queue.get()
            try:
                passed = await self._tester.site_filter(batch)
                for rec in passed:
                    await self._pool.upsert(rec)
                if self._stats is not None:
                    self._stats.total_entered += len(passed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("sync test batch failed")
            finally:
                self._queue.task_done()

    def _enqueue(self, batch: list) -> None:
        """入队待测批次；队满时丢弃最旧批次，保证内存有界并累计 drops。"""
        try:
            self._queue.put_nowait(batch)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                return
            self._drops += 1
            logger.warning(
                "sync test queue full, dropped oldest pending batch (drops=%d)",
                self._drops,
            )
            try:
                self._queue.put_nowait(batch)
            except asyncio.QueueFull:
                pass

    async def join(self) -> None:
        """等待全部已入队批次被测试完成（供测试/排空使用）。"""
        await self._queue.join()

    async def run(self) -> None:
        """拉取主循环；同时启动并持有全部测试 worker 的生命周期。

        按 tick 起点计时维护 ``interval`` 节奏，慢测试不阻塞拉取；单 tick
        异常仅记日志，不影响池内现有记录；支持取消。
        """
        workers = [
            asyncio.create_task(self._run_worker())
            for _ in range(self._worker_count())
        ]
        try:
            while True:
                start = time.monotonic()
                try:
                    await self._sync_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("sync tick failed")
                elapsed = time.monotonic() - start
                await self._sleep(max(0.0, self._interval - elapsed))
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)