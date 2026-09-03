"""HTTP API 路由：status / count / ips / ips-after-id。

统一响应 ``{code, msg, data}``（ip_pool_common.api.ok）。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Request

from ip_pool_common.api import ok
from ip_pool_common.models import IpRecord

router = APIRouter(prefix="/api/v1", tags=["level1"])


def _record_to_dict(rec: IpRecord) -> dict:
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


def _records_to_list(records: list[IpRecord]) -> list[dict]:
    return [_record_to_dict(r) for r in records]


@router.get("/status")
async def status(request: Request):
    pool = request.app.state.pool
    counts = pool.counts()
    stats = request.app.state.stats
    return ok(
        {
            "uptime": round(time.time() - request.app.state.start_time, 3),
            "total_pulled": stats.total_pulled,
            "total_entered": stats.total_entered,
            "total_duplicates": request.app.state.pool.duplicates,
            "pool_size": counts.total,
            "counts": {
                "http": counts.http,
                "https": counts.https,
                "socks4": counts.socks4,
                "socks5": counts.socks5,
            },
            "api_call_count": getattr(request.app.state, "api_call_count", 0),
            "next_id": pool.next_id,
            "providers": [
                {
                    "name": ps.name,
                    "type": ps.type,
                    "total_pulled": ps.total_pulled,
                    "total_entered": ps.total_entered,
                    "pull_failures": ps.pull_failures,
                    "test_failures": ps.test_failures,
                    "drops": ps.drops,
                }
                for ps in getattr(request.app.state, "provider_stats", {}).values()
            ],
            "errors": {
                "pull_failures": stats.pull_failures,
                "test_failures": stats.test_failures,
                "ttl_sweep_failures": stats.ttl_sweep_failures,
            },
            "drops": stats.drops,
        }
    )


@router.get("/count")
async def count(request: Request):
    counts = request.app.state.pool.counts()
    return ok(
        {
            "pool_size": counts.total,
            "counts": {
                "http": counts.http,
                "https": counts.https,
                "socks4": counts.socks4,
                "socks5": counts.socks5,
            },
        }
    )


@router.get("/ips")
async def ips(request: Request):
    records = await request.app.state.pool.all()
    return ok(_records_to_list(records))


@router.get("/ips/after/{id_}")
async def ips_after(id_: int, request: Request):
    pool = request.app.state.pool
    records = await pool.after(id_)
    return ok(_records_to_list(records), max_id=pool.max_id)
