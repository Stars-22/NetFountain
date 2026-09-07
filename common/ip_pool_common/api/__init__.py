"""API 通用件：统一错误码、响应封装、ASGI 中间件、统一启动入口。

子模块划分：``codes``（错误码）/ ``responses``（响应封装）/
``middleware``（计数与业务码日志）/ ``runner``（启动入口）。
"""
from __future__ import annotations

from .codes import ErrorCode
from .middleware import ApiCounterMiddleware, BizCodeLogMiddleware
from .responses import err, ok
from .runner import run_app

__all__ = [
    "ApiCounterMiddleware",
    "BizCodeLogMiddleware",
    "ErrorCode",
    "err",
    "ok",
    "run_app",
]
