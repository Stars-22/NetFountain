"""acquire 相关 query 参数解析（统一转 40000 PARAM_ERROR）。"""
from __future__ import annotations

import math

from starlette.datastructures import QueryParams

from ..core.pool import AcquireStrategy

__all__ = [
    "ParamError",
    "parse_acquire_query",
    "parse_count",
    "parse_non_negative_float",
    "parse_strategy",
]


class ParamError(ValueError):
    """query 参数校验失败（统一转 40000 PARAM_ERROR）。"""


def parse_strategy(qp: QueryParams) -> AcquireStrategy:
    raw = qp.get("strategy")
    if raw is None:
        return AcquireStrategy.LATEST
    try:
        return AcquireStrategy(raw)
    except ValueError:
        raise ParamError(f"invalid strategy: {raw}") from None


def parse_non_negative_float(qp: QueryParams, name: str) -> float | None:
    raw = qp.get(name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ParamError(f"invalid {name}: {raw}") from None
    if math.isnan(value) or value < 0:
        raise ParamError(f"{name} must be a number >= 0")
    return value


def parse_count(qp: QueryParams) -> int:
    raw = qp.get("count")
    if raw is None:
        raise ParamError("missing required query param: count")
    try:
        value = int(raw)
    except ValueError:
        raise ParamError(f"invalid count: {raw}") from None
    if value < 1:
        raise ParamError("count must be an integer >= 1")
    return value


def parse_acquire_query(qp: QueryParams) -> tuple:
    """解析策略与筛选参数 → ``(strategy, max_latency_ms, min_remaining_sec)``。"""
    return (
        parse_strategy(qp),
        parse_non_negative_float(qp, "max_latency_ms"),
        parse_non_negative_float(qp, "min_remaining_sec"),
    )
