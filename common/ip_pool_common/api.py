"""API 通用件：统一错误码、响应封装、调用计数中间件、业务码日志中间件、统一启动入口。"""
from __future__ import annotations

import json
import logging
import threading
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class ErrorCode(IntEnum):
    """统一错误码。"""

    OK = 0
    PARAM_ERROR = 40000
    NOT_FOUND = 40400          # 站点未配置/对象不存在
    EMPTY_POOL = 40402         # 二级池 acquire 空池
    INTERNAL = 50000
    UPSTREAM_ERROR = 50200     # 代理层转发上游故障


def ok(data: Any = None, **extra: Any) -> dict[str, Any]:
    """统一成功响应：``{"code": 0, "msg": "ok", "data": data, **extra}``。

    ``extra`` 用于附加顶层业务字段（如增量接口的 ``max_id``），不破坏原契约。
    """
    return {"code": ErrorCode.OK, "msg": "ok", "data": data, **extra}


def err(code: int | ErrorCode, msg: str) -> dict[str, Any]:
    """统一失败响应：``{"code": code, "msg": msg, "data": None}``。"""
    return {"code": int(code), "msg": msg, "data": None}


class ApiCounterMiddleware:
    """API 调用计数中间件（纯 ASGI，thread/async 安全）。

    用法：``app.add_middleware(ApiCounterMiddleware)``，
    通过 ``middleware.count`` 读取累计调用次数。
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self._lock = threading.Lock()
        self._count = 0

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            with self._lock:
                self._count += 1
        await self.app(scope, receive, send)

    @property
    def count(self) -> int:
        with self._lock:
            return self._count


def _extract_code(raw: bytes) -> int | str:
    """从响应 body 解析业务码；非 JSON 或无 ``code`` 字段返回 ``"-"``。"""
    if not raw:
        return "-"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "-"
    if isinstance(data, dict) and isinstance(data.get("code"), int):
        return data["code"]
    return "-"


class BizCodeLogMiddleware:
    """为每个 HTTP 请求追加一条含返回业务码的日志（纯 ASGI）。

    - 包装 ``send`` 收集响应状态码与 body 字节，响应结束后解析 ``code``；
    - 日志格式：``http=<状态码> biz=<业务码> method=<方法> path=<路径>``；
    - 不改响应、不吞异常；非 http scope 不记录；body 非 JSON 时业务码记 ``"-"``。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status: int | None = None
        body = bytearray()

        async def wrapped_send(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message.get("status")
            elif message["type"] == "http.response.body":
                body.extend(message.get("body") or b"")
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            logger.info(
                "http=%s biz=%s method=%s path=%s",
                status if status is not None else "-",
                _extract_code(bytes(body)),
                scope.get("method", "-"),
                scope.get("path", "-"),
            )


async def run_app(app: Any, host: str, port: int) -> None:
    """统一 uvicorn 启动入口（uvicorn 延迟导入）。"""
    import uvicorn  # noqa: PLC0415

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()