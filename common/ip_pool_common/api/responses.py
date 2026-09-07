"""统一响应封装：``{code, msg, data}`` 契约。"""
from __future__ import annotations

from typing import Any

from .codes import ErrorCode

__all__ = ["err", "ok"]


def ok(data: Any = None, **extra: Any) -> dict[str, Any]:
    """统一成功响应：``{"code": 0, "msg": "ok", "data": data, **extra}``。

    ``extra`` 用于附加顶层业务字段（如增量接口的 ``max_id``），不破坏原契约。
    """
    return {"code": ErrorCode.OK, "msg": "ok", "data": data, **extra}


def err(code: int | ErrorCode, msg: str) -> dict[str, Any]:
    """统一失败响应：``{"code": code, "msg": msg, "data": None}``。"""
    return {"code": int(code), "msg": msg, "data": None}
