"""服务运行统计（status 快照）。"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ServiceStats"]


@dataclass
class ServiceStats:
    """服务运行统计（status 快照）。"""

    uptime: float = 0.0
    total_pulled: int = 0
    total_entered: int = 0
    api_call_count: int = 0
    last_synced_id: int | None = None
    sync_failures: int = 0
    test_failures: int = 0
    revalidate_failures: int = 0
    ttl_sweep_failures: int = 0
    drops: int = 0
    empty_acquires: int = 0
