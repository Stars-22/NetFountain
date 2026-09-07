"""供应商基类：模板方法 ``pull`` + 注册工厂 ``ProviderFactory``。

统一假设的供应商响应格式（写入注释供各测试桩/联调沿用）：

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

模板方法契约：子类只实现 ``_params``（构造查询参数）与 ``_parse``
（解析响应体），HTTP 请求、超时控制与异常包装由基类统一处理。
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import aiohttp

from ip_pool_common.models import Protocol, ProviderIp

from ..config import ProviderConfig

logger = logging.getLogger(__name__)

__all__ = [
    "BaseProvider",
    "ProviderFactory",
    "coerce_protocol",
    "items_list",
    "parse_host_port",
    "parse_optional_float",
    "payload_dict",
    "register",
]


def coerce_protocol(value: Any) -> Protocol:
    """将供应商协议字符串归一化为 Protocol 枚举；未知/缺失值回退 HTTP。"""
    if value is None:
        return Protocol.HTTP
    try:
        return Protocol(str(value).strip().lower())
    except ValueError:
        return Protocol.HTTP


def parse_host_port(item: Any) -> tuple[str, int] | None:
    """提取 ``(ip, port)``；item 非对象 / ip 非空串 / port 无法转 int 时返回 None。"""
    if not isinstance(item, dict):
        return None
    ip = item.get("ip")
    if not isinstance(ip, str) or not ip.strip():
        return None
    try:
        port = int(item.get("port"))
    except (TypeError, ValueError):
        return None
    return ip, port


def parse_optional_float(value: Any) -> float | None:
    """宽松转 float；None/非法值返回 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def payload_dict(payload: Any) -> dict | None:
    """payload 必须是顶层对象，否则记日志返回 None。"""
    if isinstance(payload, dict):
        return payload
    logger.warning("provider payload is not an object: %r", type(payload).__name__)
    return None


def items_list(container: dict, key: str) -> list | None:
    """从容器提取列表字段；缺失或非列表记日志返回 None。"""
    value = container.get(key)
    if isinstance(value, list):
        return value
    logger.warning("provider payload missing %r list", key)
    return None


class BaseProvider(ABC):
    """供应商基类。"""

    name: ClassVar[str] = ""

    def __init__(self, cfg: ProviderConfig, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session

    async def pull(self, count: int) -> list[ProviderIp]:
        """模板方法：构建参数 → GET 请求 → 解析响应。

        网络/超时/HTTP/解析异常统一记日志后原样抛出；
        ``asyncio.CancelledError`` 不拦截。
        """
        timeout = aiohttp.ClientTimeout(total=self.cfg.pull_timeout)
        try:
            async with self.session.get(
                self.cfg.api_url,
                params=self._params(count),
                timeout=timeout,
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

    @abstractmethod
    def _params(self, count: int) -> dict[str, str]:
        """构造本次拉取请求的查询参数。"""

    @abstractmethod
    def _parse(self, payload: Any, count: int) -> list[ProviderIp]:
        """解析供应商响应体为规范化 ``ProviderIp`` 列表。"""

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
