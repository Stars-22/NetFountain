"""HTTP API 路由：status / count / ips / acquire / release / delete / release-all。

统一响应 ``{code, msg, data}``（ip_pool_common.api.ok / err）。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Request

from ip_pool_common.api import ErrorCode, err, ok
from ip_pool_common.models import Level2Record

from .pool import PoolStats

router = APIRouter(prefix="/api/v1", tags=["level2"])


def _record_to_dict(rec: Level2Record) -> dict:
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


def _pool_stats_to_dict(stats: PoolStats) -> dict:
    return {
        "total": stats.total,
        "by_proto": {k.value: v for k, v in stats.by_proto.items()},
        "leased_total": stats.leased_total,
        "leased_by_proto": {k.value: v for k, v in stats.leased_by_proto.items()},
        "free_total": stats.free_total,
        "free_by_proto": {k.value: v for k, v in stats.free_by_proto.items()},
    }


def _service_stats(request: Request) -> dict:
    stats = request.app.state.stats
    return {
        "uptime": round(time.time() - request.app.state.start_time, 3),
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


@router.get("/status")
async def status(request: Request):
    data = _service_stats(request)
    data["pool_stats"] = _pool_stats_to_dict(request.app.state.pool.stats())
    return ok(data)


@router.get("/count")
async def count(request: Request):
    return ok(_pool_stats_to_dict(request.app.state.pool.stats()))


@router.get("/ips")
async def ips(request: Request):
    return ok([_record_to_dict(r) for r in request.app.state.pool.all()])


@router.post("/ips/acquire")
async def acquire(request: Request):
    rec = await request.app.state.pool.acquire()
    if rec is None:
        request.app.state.stats.empty_acquires += 1
        return err(ErrorCode.EMPTY_POOL, "empty pool: no free ip available")
    return ok(_record_to_dict(rec))


@router.post("/ips/{id_}/release")
async def release(id_: int, request: Request):
    released = await request.app.state.pool.release(id_)
    if not released:
        return err(ErrorCode.NOT_FOUND, f"record not found: {id_}")
    return ok(True)


@router.delete("/ips/{id_}")
async def delete(id_: int, request: Request):
    removed = await request.app.state.pool.remove(id_)
    if not removed:
        return err(ErrorCode.NOT_FOUND, f"record not found: {id_}")
    return ok(True)


@router.post("/ips/release-all")
async def release_all(request: Request):
    count = await request.app.state.pool.release_all()
    return ok(count)