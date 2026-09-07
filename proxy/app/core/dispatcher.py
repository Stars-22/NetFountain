"""透传分发：按站点把请求转发到对应二级池，响应原样返回。

纯透传网关：不解析业务字段，{code,msg,data} 原样返回；不缓存 IP、
不参与租赁逻辑；单站点故障不影响其它站点。
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp

logger = logging.getLogger(__name__)

__all__ = ["Dispatcher", "SiteNotFound", "UpstreamError", "strip_site"]

_API_PREFIX = "/api/v1/"


class SiteNotFound(Exception):
    """站点未配置（调用方转 NOT_FOUND 错误码）。"""

    def __init__(self, site: str) -> None:
        super().__init__(f"site not configured: {site}")
        self.site = site


class UpstreamError(Exception):
    """上游二级池不可达 / 超时 / 响应异常（调用方转 UPSTREAM_ERROR 错误码）。"""


class Dispatcher:
    """透传分发器：路由到站点对应二级池，原样返回上游状态码与 json。"""

    def __init__(
        self,
        registry,
        session: aiohttp.ClientSession,
        timeout: float = 10.0,
    ) -> None:
        self.registry = registry
        self.session = session
        self.timeout = timeout

    async def forward(
        self,
        site: str,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> tuple[int, dict]:
        """转发请求到站点对应二级池；返回 ``(上游状态码, 上游 json)``。

        - 站点未配置 → 抛 ``SiteNotFound``；
        - 上游不可达 / 超时 → 抛 ``UpstreamError``。
        """
        route = self.registry.get(site)
        if route is None:
            raise SiteNotFound(site)
        upstream_path = strip_site(site, path)
        target = route.base_url.rstrip("/") + upstream_path
        try:
            async with self.session.request(
                method,
                target,
                params=params,
                json=json_body,
                timeout=self.timeout,
            ) as resp:
                try:
                    body = await resp.json()
                except (ValueError, aiohttp.ContentTypeError) as exc:
                    raise UpstreamError(
                        f"invalid upstream response from {site}: {target}"
                    ) from exc
                return resp.status, body
        except (aiohttp.ClientError, ConnectionError, asyncio.TimeoutError) as exc:
            raise UpstreamError(f"upstream request failed for site {site!r}: {exc}") from exc


def strip_site(site: str, path: str) -> str:
    """剥离路径中的站点段：``/api/v1/{site}/ips/acquire`` → ``/api/v1/ips/acquire``。"""
    if not path.startswith(_API_PREFIX):
        return path
    rest = path[len(_API_PREFIX):]
    if not rest:
        return path
    seg, sep, tail = rest.partition("/")
    if seg != site:
        return path
    return _API_PREFIX + tail if sep else _API_PREFIX
