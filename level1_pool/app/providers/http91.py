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
- ``code != 0`` 仅记日志并返回空列表；网络/超时/HTTP/解析异常抛出（基类统一处理）。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from ip_pool_common.models import Protocol, ProviderIp

from .base import (
    BaseProvider,
    items_list,
    parse_host_port,
    payload_dict,
    register,
)

logger = logging.getLogger(__name__)

__all__ = ["Http91Provider"]


@register("http91")
class Http91Provider(BaseProvider):
    """91HTTP 供应商。"""

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

    def _parse(self, payload: Any, count: int, now: float | None = None) -> list[ProviderIp]:
        """解析响应体；``now`` 可注入（供测试固定 expire_time 换算基准）。"""
        data = payload_dict(payload)
        if data is None:
            return []
        code = data.get("code")
        if code != 0:
            logger.warning("provider error: code=%r msg=%r", code, data.get("msg"))
            return []
        body = data.get("data")
        if not isinstance(body, dict):
            logger.warning("provider payload missing 'data' object")
            return []
        proxy_list = items_list(body, "proxy_list")
        if proxy_list is None:
            return []
        if now is None:
            now = time.time()
        out: list[ProviderIp] = []
        for item in proxy_list[:count]:
            hp = parse_host_port(item)
            if hp is None:
                continue
            ip, port = hp
            out.append(
                ProviderIp(
                    ip=ip,
                    port=port,
                    protocol=Protocol.SOCKS5 if self.cfg.protocol == 2 else Protocol.HTTP,
                    ttl=self._parse_ttl(item.get("expire_time"), now),
                )
            )
        return out

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
