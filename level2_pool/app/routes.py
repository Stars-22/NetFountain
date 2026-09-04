"""HTTP API 路由：status / count / ips / acquire / acquire-batch / release /
delete / release-all。

统一响应 ``{code, msg, data}``（ip_pool_common.api.ok / err）。

acquire / acquire-batch 支持可选 query 参数（均默认关闭，不带参数 = 旧行为）：

- ``strategy``：提取策略 ``latest``（默认）/ ``random`` / ``latency_asc`` /
  ``remaining_desc``；
- ``max_latency_ms``：延迟上限筛选（``latency_ms <= 值``）；
- ``min_remaining_sec``：剩余时间下限筛选（``created_at + ttl - now >= 值``，
  ``ttl=None`` 视为永不过期恒通过）。
"""
from __future__ import annotations

import math
import time

from fastapi import APIRouter, Request
from starlette.datastructures import QueryParams

from ip_pool_common.api import ErrorCode, err, ok
from ip_pool_common.models import Level2Record

from .pool import AcquireStrategy, PoolStats

router = APIRouter(prefix="/api/v1", tags=["level2"])

_EMPTY_POOL_MSG = "empty pool: no free ip available"


class _ParamError(ValueError):
    """query 参数校验失败（统一转 40000 PARAM_ERROR）。"""


def _parse_strategy(qp: QueryParams) -> AcquireStrategy:
    raw = qp.get("strategy")
    if raw is None:
        return AcquireStrategy.LATEST
    try:
        return AcquireStrategy(raw)
    except ValueError:
        raise _ParamError(f"invalid strategy: {raw}") from None


def _parse_non_negative_float(qp: QueryParams, name: str) -> float | None:
    raw = qp.get(name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise _ParamError(f"invalid {name}: {raw}") from None
    if math.isnan(value) or value < 0:
        raise _ParamError(f"{name} must be a number >= 0")
    return value


def _parse_count(qp: QueryParams) -> int:
    raw = qp.get("count")
    if raw is None:
        raise _ParamError("missing required query param: count")
    try:
        value = int(raw)
    except ValueError:
        raise _ParamError(f"invalid count: {raw}") from None
    if value < 1:
        raise _ParamError("count must be an integer >= 1")
    return value


def _parse_acquire_query(qp: QueryParams) -> tuple:
    """解析策略与筛选参数 → ``(strategy, max_latency_ms, min_remaining_sec)``。"""
    return (
        _parse_strategy(qp),
        _parse_non_negative_float(qp, "max_latency_ms"),
        _parse_non_negative_float(qp, "min_remaining_sec"),
    )


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
    qp = request.query_params
    try:
        strategy, max_latency_ms, min_remaining_sec = _parse_acquire_query(qp)
    except _ParamError as exc:
        return err(ErrorCode.PARAM_ERROR, str(exc))
    rec = await request.app.state.pool.acquire(
        strategy,
        max_latency_ms=max_latency_ms,
        min_remaining_sec=min_remaining_sec,
    )
    if rec is None:
        request.app.state.stats.empty_acquires += 1
        return err(ErrorCode.EMPTY_POOL, _EMPTY_POOL_MSG)
    return ok(_record_to_dict(rec))


@router.post("/ips/acquire-batch")
async def acquire_batch(request: Request):
    qp = request.query_params
    try:
        count = _parse_count(qp)
        strategy, max_latency_ms, min_remaining_sec = _parse_acquire_query(qp)
    except _ParamError as exc:
        return err(ErrorCode.PARAM_ERROR, str(exc))
    records = await request.app.state.pool.acquire_batch(
        count,
        strategy,
        max_latency_ms=max_latency_ms,
        min_remaining_sec=min_remaining_sec,
    )
    if not records:
        request.app.state.stats.empty_acquires += 1
        return err(ErrorCode.EMPTY_POOL, _EMPTY_POOL_MSG)
    return ok([_record_to_dict(rec) for rec in records])


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