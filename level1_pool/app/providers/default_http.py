"""默认 HTTP 供应商：GET api_url（带 api_key），解析统一响应格式。"""
from __future__ import annotations

from typing import Any

from ip_pool_common.models import ProviderIp

from .base import (
    BaseProvider,
    coerce_protocol,
    items_list,
    parse_host_port,
    parse_optional_float,
    payload_dict,
    register,
)

__all__ = ["DefaultHttpProvider"]


@register("default_http")
class DefaultHttpProvider(BaseProvider):
    """默认 HTTP 供应商：GET api_url（带 api_key），解析统一响应格式。"""

    def _params(self, count: int) -> dict[str, str]:
        params: dict[str, str] = {"count": str(count)}
        if self.cfg.api_key:
            params["api_key"] = self.cfg.api_key
        return params

    def _parse(self, payload: Any, count: int) -> list[ProviderIp]:
        data = payload_dict(payload)
        if data is None:
            return []
        items = items_list(data, "data")
        if items is None:
            return []
        out: list[ProviderIp] = []
        for item in items[:count]:
            hp = parse_host_port(item)
            if hp is None:
                continue
            ip, port = hp
            region = item.get("region")
            out.append(
                ProviderIp(
                    ip=ip,
                    port=port,
                    protocol=coerce_protocol(item.get("protocol")),
                    region=region
                    if isinstance(region, str) and region.strip()
                    else None,
                    ttl=parse_optional_float(item.get("ttl")),
                )
            )
        return out
