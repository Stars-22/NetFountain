"""HTTP API 层：/health 聚合端点 + 按站点透传端点（表驱动注册）。"""
from __future__ import annotations

from .routes import router

__all__ = ["router"]
