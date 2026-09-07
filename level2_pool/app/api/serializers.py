"""Level2Record / PoolStats / ServiceStats → dict 序列化（HTTP 响应契约）。"""
from __future__ import annotations

import time

from ip_pool_common.models import Level2Record

from ..core.pool import PoolStats
from ..core.stats import ServiceStats

__all__ = ["pool_stats_to_dict", "record_to_dict", "service_stats_dict"]


def record_to_dict(rec: Level2Record) -> dict:
    return {
        "id": rec.id,
        "ip": rec.ip,
        "port": rec.port,
        "protocol": rec.protocol.value,
        "proxy_url": rec.proxy_url,
        "latency_ms": rec.latency_ms,
        "leased": rec.leased,
        "ttl": rec.ttl,
        "created_at": rec.created_at,
    }


def records_to_list(records: list[Level2Record]) -> list[dict]:
    return [record_to_dict(r) for r in records]


def pool_stats_to_dict(stats: PoolStats) -> dict:
    return {
        "total": stats.total,
        "by_proto": {k.value: v for k, v in stats.by_proto.items()},
        "leased_total": stats.leased_total,
        "leased_by_proto": {k.value: v for k, v in stats.leased_by_proto.items()},
        "free_total": stats.free_total,
        "free_by_proto": {k.value: v for k, v in stats.free_by_proto.items()},
    }


def service_stats_dict(stats: ServiceStats, start_time: float) -> dict:
    return {
        "uptime": round(time.time() - start_time, 3),
        "total_pulled": stats.total_pulled,
        "total_entered": stats.total_entered,
        "api_call_count": stats.api_call_count,
        "last_synced_id": stats.last_synced_id,
        "errors": {
            "sync_failures": stats.sync_failures,
            "test_failures": stats.test_failures,
            "revalidate_failures": stats.revalidate_failures,
            "ttl_sweep_failures": stats.ttl_sweep_failures,
            "empty_acquires": stats.empty_acquires,
        },
        "drops": stats.drops,
    }
