"""统一错误码。"""
from __future__ import annotations

from enum import IntEnum

__all__ = ["ErrorCode"]


class ErrorCode(IntEnum):
    """统一错误码。"""

    OK = 0
    PARAM_ERROR = 40000
    NOT_FOUND = 40400          # 站点未配置/对象不存在
    EMPTY_POOL = 40402         # 二级池 acquire 空池
    INTERNAL = 50000
    UPSTREAM_ERROR = 50200     # 代理层转发上游故障
