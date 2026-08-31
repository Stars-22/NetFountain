"""租赁池：二级池核心数据结构。

- 唯一键 = ``proxy_url``（ip+port+protocol 身份），同名记录去重；
- 本地自增 id（不复用）供 API ``release/delete/{id}`` 精确引用；
- ``acquire`` 最新优先：从入池顺序尾部向前扫描首个空闲项并原子标记租赁；
- 租赁无过期时间，仅可被显式 ``release/remove/release_all`` 解除；
- 所有变更在 ``asyncio.Lock`` 下进行，保证并发安全与分配原子性。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from ip_pool_common.models import Level2Record, Protocol


@dataclass
class PoolStats:
    """池内 / 已租赁 / 空闲 的计数（含按协议细分）。"""

    total: int
    by_proto: dict[Protocol, int]
    leased_total: int
    leased_by_proto: dict[Protocol, int]
    free_total: int
    free_by_proto: dict[Protocol, int]


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


class Level2Pool:
    """站点级租赁池：以 ``proxy_url`` 为唯一键，维护入池顺序与本地 id 索引。"""

    def __init__(self) -> None:
        self._records: dict[str, Level2Record] = {}
        self._order: list[str] = []
        self._id_index: dict[int, str] = {}
        self._next_id: int = 0
        self._lock = asyncio.Lock()

    @property
    def next_id(self) -> int:
        return self._next_id

    async def upsert(self, rec: Level2Record) -> Level2Record:
        """按 ``proxy_url`` 去重：已存在则刷新延迟/ttl/ip/port/时间，保持 id 与租赁态；
        不存在则分配本地自增 id 并加入入池顺序尾部。"""
        async with self._lock:
            key = rec.proxy_url
            existing = self._records.get(key)
            if existing is not None:
                existing.latency_ms = rec.latency_ms
                existing.ip = rec.ip
                existing.port = rec.port
                existing.protocol = rec.protocol
                existing.region = rec.region
                existing.ttl = rec.ttl
                existing.created_at = rec.created_at
                existing.last_verified_at = rec.last_verified_at
                return existing
            new_id = self._next_id
            self._next_id += 1
            stored = Level2Record(
                id=new_id,
                ip=rec.ip,
                port=rec.port,
                protocol=rec.protocol,
                proxy_url=rec.proxy_url,
                region=rec.region,
                ttl=rec.ttl,
                latency_ms=rec.latency_ms,
                leased=False,
                leased_at=None,
                created_at=rec.created_at,
                last_verified_at=rec.last_verified_at,
            )
            self._records[key] = stored
            self._order.append(key)
            self._id_index[new_id] = key
            return stored

    async def acquire(self) -> Level2Record | None:
        """最新优先：从入池顺序尾部向前扫描首个空闲项，原子标记租赁并返回；全被租赁返回 None。"""
        async with self._lock:
            for key in reversed(self._order):
                rec = self._records[key]
                if not rec.leased:
                    rec.leased = True
                    rec.leased_at = time.time()
                    return rec
            return None

    async def release(self, id_: int) -> bool:
        """经本地 id 定位，解除租赁；无此 id 返回 False。"""
        async with self._lock:
            key = self._id_index.get(id_)
            if key is None:
                return False
            rec = self._records[key]
            rec.leased = False
            rec.leased_at = None
            return True

    async def remove(self, id_: int) -> bool:
        """删除记录（含解除租赁）；无此 id 返回 False。"""
        async with self._lock:
            key = self._id_index.get(id_)
            if key is None:
                return False
            del self._records[key]
            self._order.remove(key)
            del self._id_index[id_]
            return True

    async def release_all(self) -> int:
        """解除全部租赁，返回解除数量。"""
        async with self._lock:
            count = 0
            for rec in self._records.values():
                if rec.leased:
                    rec.leased = False
                    rec.leased_at = None
                    count += 1
            return count

    def all(self) -> list[Level2Record]:
        """按入池顺序返回全部记录；纯查询，不改任何状态。"""
        return [self._records[key] for key in self._order]

    def stats(self) -> PoolStats:
        """池内 / 已租赁 / 空闲 四类计数（按协议细分）。"""
        by_proto: dict[Protocol, int] = {}
        leased_by_proto: dict[Protocol, int] = {}
        free_by_proto: dict[Protocol, int] = {}
        for rec in self._records.values():
            by_proto[rec.protocol] = by_proto.get(rec.protocol, 0) + 1
            if rec.leased:
                leased_by_proto[rec.protocol] = leased_by_proto.get(rec.protocol, 0) + 1
            else:
                free_by_proto[rec.protocol] = free_by_proto.get(rec.protocol, 0) + 1
        return PoolStats(
            total=len(self._records),
            by_proto=by_proto,
            leased_total=sum(leased_by_proto.values()),
            leased_by_proto=leased_by_proto,
            free_total=sum(free_by_proto.values()),
            free_by_proto=free_by_proto,
        )

    async def sweep_ttl(self, now: float) -> int:
        """仅当 ttl 非 None 且 ``created_at + ttl < now`` 时删除过期项，返回删除数量。"""
        async with self._lock:
            expired = [
                key
                for key, rec in self._records.items()
                if rec.ttl is not None and rec.created_at + rec.ttl < now
            ]
            for key in expired:
                rec = self._records.pop(key)
                self._order.remove(key)
                del self._id_index[rec.id]
            return len(expired)
