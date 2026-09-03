"""供应商客户端：BaseProvider 抽象 + DefaultHttpProvider + Http91Provider
+ FreeProxyProvider + ProviderFactory。

本阶段统一假设的供应商响应格式（写入注释供各测试桩/联调沿用）：

    {
      "data": [
        {"ip": "1.2.3.4", "port": 8080, "protocol": "http",
         "region": "CN-Guangdong", "ttl": 120}
      ]
    }

- ``protocol`` 缺省视为 http；``region``/``ttl`` 可空；
- 返回数量不超过请求的 ``count``；
- 网络/超时/HTTP 错误/解析异常抛出（由 PullTask 计入 ``pull_failures``）；
  成功（含空结果）正常返回列表。

扩展性：新增供应商只需「继承 BaseProvider + @register("名字")」，主流程零改动。
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, ClassVar

import aiohttp

from ip_pool_common.models import Protocol, ProviderIp

from .config import ProviderConfig

logger = logging.getLogger(__name__)


def _coerce_protocol(value: Any) -> Protocol:
    """将供应商协议字符串归一化为 Protocol 枚举；未知/缺失值回退 HTTP。"""
    if value is None:
        return Protocol.HTTP
    try:
        return Protocol(str(value).strip().lower())
    except ValueError:
        return Protocol.HTTP


class BaseProvider(ABC):
    """供应商基类。"""

    name: ClassVar[str] = ""

    def __init__(self, cfg: ProviderConfig, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session

    @abstractmethod
    async def pull(self, count: int) -> list[ProviderIp]:
        """从供应商拉取至多 ``count`` 个 IP。"""

    async def close(self) -> None:
        """释放供应商自有资源；session 生命周期由装配方统一管理，可重复调用。"""


class ProviderFactory:
    """供应商工厂：按 ``type`` 名实例化已注册的供应商子类。"""

    _registry: dict[str, type[BaseProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_cls: type[BaseProvider]) -> None:
        cls._registry[name] = provider_cls

    @classmethod
    def create(
        cls,
        provider_type: str,
        cfg: ProviderConfig,
        session: aiohttp.ClientSession,
    ) -> BaseProvider:
        provider_cls = cls._registry.get(provider_type)
        if provider_cls is None:
            available = ", ".join(sorted(cls._registry)) if cls._registry else "none"
            raise ValueError(
                f"unknown provider type: {provider_type!r} (available: {available})"
            )
        return provider_cls(cfg, session)


def register(name: str):
    """类装饰器：将供应商类型注册进 ProviderFactory，并记录类型名。"""

    def _deco(cls: type[BaseProvider]) -> type[BaseProvider]:
        ProviderFactory.register(name, cls)
        cls.name = name
        return cls

    return _deco


@register("default_http")
class DefaultHttpProvider(BaseProvider):
    """默认 HTTP 供应商：GET api_url（带 api_key），解析统一响应格式。"""

    async def pull(self, count: int) -> list[ProviderIp]:
        params: dict[str, str] = {"count": str(count)}
        if self.cfg.api_key:
            params["api_key"] = self.cfg.api_key
        timeout = aiohttp.ClientTimeout(total=self.cfg.pull_timeout)
        try:
            async with self.session.get(
                self.cfg.api_url, params=params, timeout=timeout
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
            return self._parse(payload, count)
        except asyncio.CancelledError:
            raise
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ValueError,
            TypeError,
            OSError,
        ) as exc:
            logger.warning("pull from %r failed: %s", self.cfg.api_url, exc)
            raise

    def _parse(self, payload: Any, count: int) -> list[ProviderIp]:
        if not isinstance(payload, dict):
            logger.warning("provider payload is not an object: %r", type(payload).__name__)
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            logger.warning("provider payload missing 'data' list")
            return []
        out: list[ProviderIp] = []
        for item in data[:count]:
            parsed = self._parse_item(item)
            if parsed is not None:
                out.append(parsed)
        return out

    def _parse_item(self, item: Any) -> ProviderIp | None:
        if not isinstance(item, dict):
            return None
        ip = item.get("ip")
        if not isinstance(ip, str) or not ip.strip():
            return None
        try:
            port = int(item.get("port"))
        except (TypeError, ValueError):
            return None
        protocol = _coerce_protocol(item.get("protocol"))
        region = item.get("region")
        ttl: float | None = None
        if item.get("ttl") is not None:
            try:
                ttl = float(item["ttl"])
            except (TypeError, ValueError):
                ttl = None
        return ProviderIp(
            ip=ip,
            port=port,
            protocol=protocol,
            region=region if isinstance(region, str) and region.strip() else None,
            ttl=ttl,
        )


@register("http91")
class Http91Provider(BaseProvider):
    """91HTTP 供应商：GET /v1/get-ip（json 格式，携带代理过期时间）。

    响应结构（与 Apifox 文档一致）：

        {
          "code": 0,
          "msg": "OK",
          "data": {
            "count": 10,
            "filter_count": 0,
            "surplus_quantity": 0,
            "proxy_list": [
              {"ip": "1.2.3.4", "port": 8080,
               "expire_time": "2026-08-28 17:03:06"}
            ]
          }
        }

    - 业务编号/密钥分别来自 ``cfg.trade_no`` / ``cfg.api_key``；
    - ``num`` 取请求的 ``count``，``time=1`` 使结果携带 ``expire_time``；
    - ``expire_time`` 为绝对时间，换算为剩余秒数写入 ``ProviderIp.ttl``；
    - ``code != 0`` 仅记日志并返回空列表；网络/超时/HTTP/解析异常抛出。
    """

    _EXPIRE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")

    def _params(self, count: int) -> dict[str, str]:
        return {
            "trade_no": self.cfg.trade_no,
            "secret": self.cfg.api_key,
            "num": str(count),
            "format": "json",
            "time": "1",
            "protocol": str(self.cfg.protocol),
        }

    async def pull(self, count: int) -> list[ProviderIp]:
        timeout = aiohttp.ClientTimeout(total=self.cfg.pull_timeout)
        try:
            async with self.session.get(
                self.cfg.api_url, params=self._params(count), timeout=timeout
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
            return self._parse(payload, count)
        except asyncio.CancelledError:
            raise
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ValueError,
            TypeError,
            OSError,
        ) as exc:
            logger.warning("pull from %r failed: %s", self.cfg.api_url, exc)
            raise

    def _parse(self, payload: Any, count: int, now: float | None = None) -> list[ProviderIp]:
        if not isinstance(payload, dict):
            logger.warning("provider payload is not an object: %r", type(payload).__name__)
            return []
        code = payload.get("code")
        if code != 0:
            logger.warning("provider error: code=%r msg=%r", code, payload.get("msg"))
            return []
        data = payload.get("data")
        if not isinstance(data, dict):
            logger.warning("provider payload missing 'data' object")
            return []
        proxy_list = data.get("proxy_list")
        if not isinstance(proxy_list, list):
            logger.warning("provider payload missing 'data.proxy_list' list")
            return []
        if now is None:
            now = time.time()
        out: list[ProviderIp] = []
        for item in proxy_list[:count]:
            parsed = self._parse_item(item, now)
            if parsed is not None:
                out.append(parsed)
        return out

    def _parse_item(self, item: Any, now: float) -> ProviderIp | None:
        if not isinstance(item, dict):
            return None
        ip = item.get("ip")
        if not isinstance(ip, str) or not ip.strip():
            return None
        try:
            port = int(item.get("port"))
        except (TypeError, ValueError):
            return None
        protocol = Protocol.SOCKS5 if self.cfg.protocol == 2 else Protocol.HTTP
        return ProviderIp(
            ip=ip,
            port=port,
            protocol=protocol,
            ttl=self._parse_ttl(item.get("expire_time"), now),
        )

    @staticmethod
    def _parse_ttl(expire_time: Any, now: float) -> float | None:
        """将 ``expire_time`` 换算为剩余秒数；无法解析时返回 None。"""
        if not isinstance(expire_time, str) or not expire_time.strip():
            return None
        text = expire_time.strip()
        for fmt in Http91Provider._EXPIRE_FORMATS:
            try:
                ts = datetime.strptime(text, fmt).timestamp()
            except ValueError:
                continue
            return max(ts - now, 0.0)
        try:
            ts = float(text)
        except (TypeError, ValueError):
            return None
        return max(ts - now, 0.0)


@register("freeproxy")
class FreeProxyProvider(BaseProvider):
    """FreeProxy（zdopen）供应商：GET /FreeProxy/Get/（JSON 提取接口）。

    响应结构（与接口文档及实测一致，``code`` 为字符串）：

        {
          "code": "10001",
          "msg": "获取成功",
          "data": {
            "count": 5,
            "proxy_list": [
              {"ip": "203.25.208.163", "port": 1100, "adr": "广东省 电信",
               "protocol": "socks5", "level": "高匿"}
            ]
          }
        }

    - ``app_id`` / ``akey`` 分别来自 ``cfg.trade_no`` / ``cfg.api_key``；
    - ``dalu``（必选：1=大陆，0=海外）与 ``protocol_type``（可选过滤，0=不发送）
      来自配置；``return_type=3`` 固定 JSON 格式；
    - 成功编号为 ``"10001"``（字符串），其余（12001 akey 错误、12002 频率过快、
      12009 无代理等）仅记日志并返回空列表；
    - ``protocol`` 直接映射 Protocol 枚举（http/socks4/socks5/https），
      ``adr`` 映射 region；``level``（匿名度）无对应字段，丢弃；
      不返回过期时间，``ttl`` 恒为 None；
    - 网络/超时/HTTP/解析异常抛出。
    """

    MAX_COUNT = 100  # 接口限制：单次提取数量最大 100

    def _params(self, count: int) -> dict[str, str]:
        params = {
            "app_id": self.cfg.trade_no,
            "akey": self.cfg.api_key,
            "count": str(min(count, self.MAX_COUNT)),
            "dalu": str(self.cfg.dalu),
            "return_type": "3",
        }
        if self.cfg.protocol_type > 0:
            params["protocol_type"] = str(self.cfg.protocol_type)
        return params

    async def pull(self, count: int) -> list[ProviderIp]:
        timeout = aiohttp.ClientTimeout(total=self.cfg.pull_timeout)
        try:
            async with self.session.get(
                self.cfg.api_url, params=self._params(count), timeout=timeout
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
            return self._parse(payload, count)
        except asyncio.CancelledError:
            raise
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ValueError,
            TypeError,
            OSError,
        ) as exc:
            logger.warning("pull from %r failed: %s", self.cfg.api_url, exc)
            raise

    def _parse(self, payload: Any, count: int) -> list[ProviderIp]:
        if not isinstance(payload, dict):
            logger.warning("provider payload is not an object: %r", type(payload).__name__)
            return []
        code = payload.get("code")
        if str(code).strip() != "10001":
            logger.warning("provider error: code=%r msg=%r", code, payload.get("msg"))
            return []
        data = payload.get("data")
        if not isinstance(data, dict):
            logger.warning("provider payload missing 'data' object")
            return []
        proxy_list = data.get("proxy_list")
        if not isinstance(proxy_list, list):
            logger.warning("provider payload missing 'data.proxy_list' list")
            return []
        out: list[ProviderIp] = []
        for item in proxy_list[:count]:
            parsed = self._parse_item(item)
            if parsed is not None:
                out.append(parsed)
        return out

    def _parse_item(self, item: Any) -> ProviderIp | None:
        if not isinstance(item, dict):
            return None
        ip = item.get("ip")
        if not isinstance(ip, str) or not ip.strip():
            return None
        try:
            port = int(item.get("port"))
        except (TypeError, ValueError):
            return None
        region = item.get("adr")
        return ProviderIp(
            ip=ip,
            port=port,
            protocol=_coerce_protocol(item.get("protocol")),
            region=region if isinstance(region, str) and region.strip() else None,
        )
