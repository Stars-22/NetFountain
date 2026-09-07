"""IpRecord → dict 序列化（HTTP 响应契约）。"""
from __future__ import annotations

from ip_pool_common.models import IpRecord

__all__ = ["record_to_dict", "records_to_list"]


def record_to_dict(rec: IpRecord) -> dict:
    return {
        "id": rec.id,
        "ip": rec.ip,
        "port": rec.port,
        "protocol": rec.protocol.value,
        "proxy_url": rec.proxy_url,
        "region": rec.region,
        "ttl": rec.ttl,
        "created_at": rec.created_at,
    }


def records_to_list(records: list[IpRecord]) -> list[dict]:
    return [record_to_dict(r) for r in records]
