"""ASGI 中间件：调用计数、业务码日志。

均支持 ``add_middleware`` 装配方式；计数中间件通过 ``path_prefix`` 与
``counter`` 参数化扩展（统计口径/落点由装配方注入），不再需要子类化。
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["ApiCounterMiddleware", "BizCodeLogMiddleware"]


class ApiCounterMiddleware:
    """API 调用计数中间件（纯 ASGI，thread/async 安全）。

    用法：``app.add_middleware(ApiCounterMiddleware, path_prefix=..., counter=...)``。

    - ``path_prefix``：仅统计匹配前缀的 http 请求；None 时统计全部
      http/websocket 请求（旧行为）；
    - ``counter``：命中时的计数回调，参数为当前 ``scope``；None 时内部累计，
      经 ``count`` 属性读取。
    """

    def __init__(
        self,
        app: Any,
        *,
        path_prefix: str | None = None,
        counter: Callable[[dict], None] | None = None,
    ) -> None:
        self.app = app
        self.path_prefix = path_prefix
        self._external_counter = counter
        self._lock = threading.Lock()
        self._count = 0

    def _hit(self, scope: dict) -> None:
        if self._external_counter is not None:
            self._external_counter(scope)
            return
        with self._lock:
            self._count += 1

    def _should_count(self, scope: dict) -> bool:
        if self.path_prefix is not None:
            return scope["type"] == "http" and scope.get("path", "").startswith(
                self.path_prefix
            )
        return scope["type"] in ("http", "websocket")

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if self._should_count(scope):
            self._hit(scope)
        await self.app(scope, receive, send)

    @property
    def count(self) -> int:
        """内部累计的调用次数（注入外部 counter 时不累计）。"""
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
