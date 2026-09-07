"""核心领域层：租赁池与运行统计（不含 IO 与 HTTP 装配）。"""
from __future__ import annotations

from .pool import AcquireStrategy, Level2Pool, PoolStats, remaining_seconds
from .stats import ServiceStats

__all__ = ["AcquireStrategy", "Level2Pool", "PoolStats", "ServiceStats", "remaining_seconds"]
