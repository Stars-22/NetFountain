"""HTTP API 路由：status / count / ips / acquire / acquire-batch / release /
delete / release-all。

统一响应 ``{code, msg, data}``（ip_pool_common.api.ok / err）。

acquire / acquire-batch 支持可选 query 参数（均默认关闭，不带参数 = 旧行为）：

- ``strategy``：提取策略 ``latest``（默认）/ ``random`` / ``latency_asc`` /
  ``remaining_desc``；
- ``max_latency_ms``：延迟上限筛选（``latency_ms <= 值``）；
- ``min_remaining_sec``：剩余时间下限（``created_at + ttl - now >= 值``，
  ``ttl=None`` 视为永不过期恒通过）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ip_pool_common.api import ErrorCode, err, ok

from ..core.pool import AcquireStrategy  # noqa: F401  # re-export 策略类型
from .params import ParamError, parse_acquire_query, parse_count
from .serializers import (
    pool_stats_to_dict,
    record_to_dict,
    records_to_list,
    service_stats_dict,
)

router = APIRouter(prefix="/api/v1", tags=["level2"])

__all__ = ["router"]

_EMPTY_POOL_MSG = "empty pool: no free ip available"


@router.get("/status")
async def status(request: Request):
    data = service_stats_dict(request.app.state.stats, request.app.state.start_time)
    data["pool_stats"] = pool_stats_to_dict(request.app.state.pool.stats())
    return ok(data)


@router.get("/count")
async def count(request: Request):
    return ok(pool_stats_to_dict(request.app.state.pool.stats()))


@router.get("/ips")
async def ips(request: Request):
    return ok(records_to_list(request.app.state.pool.all()))


@router.post("/ips/acquire")
async def acquire(request: Request):
    qp = request.query_params
    try:
        strategy, max_latency_ms, min_remaining_sec = parse_acquire_query(qp)
    except ParamError as exc:
        return err(ErrorCode.PARAM_ERROR, str(exc))
    rec = await request.app.state.pool.acquire(
        strategy,
        max_latency_ms=max_latency_ms,
        min_remaining_sec=min_remaining_sec,
    )
    if rec is None:
        request.app.state.stats.empty_acquires += 1
        return err(ErrorCode.EMPTY_POOL, _EMPTY_POOL_MSG)
    return ok(record_to_dict(rec))


@router.post("/ips/acquire-batch")
async def acquire_batch(request: Request):
    qp = request.query_params
    try:
        count = parse_count(qp)
        strategy, max_latency_ms, min_remaining_sec = parse_acquire_query(qp)
    except ParamError as exc:
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
    return ok(records_to_list(records))


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
