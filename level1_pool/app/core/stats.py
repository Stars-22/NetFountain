"""运行统计：全局服务统计与按供应商明细统计（/status 展示用）。"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ProviderStats", "ServiceStats"]


@dataclass
class ServiceStats:
    """运行统计（随请求实时快照，非热数据持久对象）。"""

    uptime: float = 0.0
    total_pulled: int = 0
    total_entered: int = 0
    api_call_count: int = 0
    next_id: int = 0
    pull_failures: int = 0
    test_failures: int = 0
    ttl_sweep_failures: int = 0
    drops: int = 0


@dataclass
class ProviderStats:
    """单个供应商运行统计（与 ServiceStats 对应子集，/status 按 providers 明细展示）。"""

    name: str = ""
    type: str = ""
    total_pulled: int = 0
    total_entered: int = 0
    pull_failures: int = 0
    test_failures: int = 0
    drops: int = 0
