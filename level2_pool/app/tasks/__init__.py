"""后台任务层：增量同步（SyncTask）、周期复验（RevalidateTask）、TTL 清扫（TtlSweeper）。

- 复验每 ``revalidate_interval``(60s) 对池内全部 IP 做代理可达性测试，
  删除不通过项（含租赁中的，按策划书语义）；
- TTL 清扫每 ``ttl_sweep_interval``(5s) 删除过期项；
- 单个 tick 的任何异常仅记日志，循环继续；支持 asyncio 取消优雅退出。
"""
from __future__ import annotations

from .revalidate import RevalidateTask
from .ttl import TtlSweeper

__all__ = ["RevalidateTask", "TtlSweeper"]
