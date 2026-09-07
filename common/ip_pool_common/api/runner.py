"""统一 uvicorn 启动入口。"""
from __future__ import annotations

from typing import Any

__all__ = ["run_app"]


async def run_app(app: Any, host: str, port: int) -> None:
    """统一 uvicorn 启动入口（uvicorn 延迟导入）。"""
    import uvicorn  # noqa: PLC0415

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
