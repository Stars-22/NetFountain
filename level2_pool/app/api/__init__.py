"""HTTP API 层：路由、query 参数解析、响应序列化。"""
from __future__ import annotations

from .routes import router

__all__ = ["router"]
