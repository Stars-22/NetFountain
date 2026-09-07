"""路由表：Registry + SiteRoute（配置文件热更新，原子替换）。

- 支持本地文件与远程 URL 两种来源（route_file / route_url）；
- 重载采用「读新 → 校验 → 原子替换」，失败保留旧表，服务不中断；
- reload_loop 每 reload_interval 秒重载一次，单次失败记日志并继续。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import aiohttp
import yaml

from ip_pool_common.config import load_yaml

logger = logging.getLogger(__name__)

__all__ = ["Registry", "SiteRoute"]

_URL_FETCH_TIMEOUT = 10.0


@dataclass
class SiteRoute:
    """单站点路由：站点标识 → 二级池服务地址。"""

    name: str
    base_url: str
    target_url: str | None = None


class Registry:
    """站点路由表：加载、热重载（原子替换）、查询。"""

    def __init__(
        self,
        route_file: str | None = None,
        route_url: str | None = None,
        reload_interval: float = 60.0,
    ) -> None:
        self.route_file = route_file
        self.route_url = route_url
        self.reload_interval = reload_interval
        self._sites: dict[str, SiteRoute] = {}
        self._session: aiohttp.ClientSession | None = None

    # -- 查询 --

    def get(self, site: str) -> SiteRoute | None:
        return self._sites.get(site)

    def sites(self) -> list[SiteRoute]:
        return list(self._sites.values())

    # -- 加载与重载 --

    async def load(self) -> int:
        """从 route_file / route_url 读取配置，校验后原子替换内部表；返回站点数。"""
        raw = await self._read()
        routes = self._parse(raw)
        self._sites = routes
        return len(routes)

    async def reload_loop(self) -> None:
        """每 reload_interval 秒重载一次；单次失败记日志并继续。"""
        while True:
            await asyncio.sleep(self.reload_interval)
            try:
                await self.load()
            except Exception:
                logger.exception("route reload failed; keeping previous table")

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    # -- 内部 --

    def _session_for_url(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _read(self) -> dict:
        if self.route_file:
            return load_yaml(self.route_file)
        if self.route_url:
            return await self._fetch_url()
        raise ValueError("no route source configured (route_file/route_url both empty)")

    async def _fetch_url(self) -> dict:
        session = self._session_for_url()
        try:
            async with session.get(
                self.route_url, timeout=aiohttp.ClientTimeout(total=_URL_FETCH_TIMEOUT)
            ) as resp:
                if resp.status != 200:
                    raise ValueError(
                        f"failed to fetch route url {self.route_url}: HTTP {resp.status}"
                    )
                text = await resp.text()
        except aiohttp.ClientError as exc:
            raise ValueError(f"failed to fetch route url {self.route_url}: {exc}") from exc
        data = yaml.safe_load(text)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError(f"route config must be a mapping at top level: {self.route_url}")
        return data

    @staticmethod
    def _parse(data: dict) -> dict[str, SiteRoute]:
        if not isinstance(data, dict):
            raise ValueError(f"route config must be a mapping: {data!r}")
        sites = data.get("sites")
        if sites is None:
            raise ValueError("route config missing 'sites' key")
        if not isinstance(sites, list):
            raise ValueError("route config 'sites' must be a list")
        routes: dict[str, SiteRoute] = {}
        for item in sites:
            if not isinstance(item, dict):
                raise ValueError(f"invalid site entry: {item!r}")
            name = item.get("name")
            base_url = item.get("base_url")
            if not name or not isinstance(name, str):
                raise ValueError(f"site entry missing valid 'name': {item!r}")
            if not base_url or not isinstance(base_url, str):
                raise ValueError(f"site {name!r} missing valid 'base_url': {item!r}")
            routes[name] = SiteRoute(
                name=name,
                base_url=base_url,
                target_url=item.get("target_url"),
            )
        return routes
