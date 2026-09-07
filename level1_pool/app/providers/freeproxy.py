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
- 网络/超时/HTTP/解析异常抛出（基类统一处理）。
"""
from __future__ import annotations

import logging
from typing import Any

from ip_pool_common.models import ProviderIp

from .base import (
    BaseProvider,
    coerce_protocol,
    items_list,
    parse_host_port,
    payload_dict,
    register,
)

logger = logging.getLogger(__name__)

__all__ = ["FreeProxyProvider"]


@register("freeproxy")
class FreeProxyProvider(BaseProvider):
    """FreeProxy（zdopen）供应商。"""

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

    def _parse(self, payload: Any, count: int) -> list[ProviderIp]:
        data = payload_dict(payload)
        if data is None:
            return []
        code = data.get("code")
        if str(code).strip() != "10001":
            logger.warning("provider error: code=%r msg=%r", code, data.get("msg"))
            return []
        body = data.get("data")
        if not isinstance(body, dict):
            logger.warning("provider payload missing 'data' object")
            return []
        proxy_list = items_list(body, "proxy_list")
        if proxy_list is None:
            return []
        out: list[ProviderIp] = []
        for item in proxy_list[:count]:
            hp = parse_host_port(item)
            if hp is None:
                continue
            ip, port = hp
            region = item.get("adr")
            out.append(
                ProviderIp(
                    ip=ip,
                    port=port,
                    protocol=coerce_protocol(item.get("protocol")),
                    region=region
                    if isinstance(region, str) and region.strip()
                    else None,
                )
            )
        return out
