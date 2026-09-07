"""一级池 HTTP 客户端：消费一级池增量契约。

- ``GET /api/v1/ips``：全量；
- ``GET /api/v1/ips/after/{id}``：按 id 增量，响应含顶层 ``max_id``
  供全量重拉判定。
"""
from __future__ import annotations

import aiohttp

from ip_pool_common.models import IpRecord, Protocol, build_proxy_url

__all__ = ["Level1SyncClient"]


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

    async def _get_payload(self, url: str) -> dict:
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
        return payload

    async def _get_items(self, url: str) -> list[dict]:
        payload = await self._get_payload(url)
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

    async def fetch_after(self, id_: int) -> tuple[list[IpRecord], int | None]:
        """GET /api/v1/ips/after/{id}：增量拉取，返回 ``(records, max_id)``。

        ``max_id`` 为一级池响应顶层字段（当前池内最大 id）；data 为空或无
        ``max_id`` 字段时返回 ``([], None)``。
        """
        payload = await self._get_payload(f"{self._base_url}/api/v1/ips/after/{id_}")
        data = payload.get("data")
        if data is None:
            data = []
        if not isinstance(data, list):
            raise ValueError(f"level1 data is not a list: /api/v1/ips/after/{id_}")
        max_id = payload.get("max_id")
        if not isinstance(max_id, int):
            max_id = None
        return [self._to_ip_record(item) for item in data], max_id
