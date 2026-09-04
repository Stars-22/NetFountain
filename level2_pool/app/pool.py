"""租赁池：二级池核心数据结构。

- 唯一键 = ``proxy_url``（ip+port+protocol 身份），同名记录去重；
- 本地自增 id（不复用）供 API ``release/delete/{id}`` 精确引用；
- ``acquire`` 支持提取策略（默认最新优先：从入池顺序尾部向前扫描首个空闲项）
  与延迟/剩余时间筛选，命中后原子标记租赁；单次提取不排序，
  直接一次扫描取 argmin/argmax；``acquire_batch`` 单锁内原子提取多条；
- 剩余时间 = ``created_at + ttl - now``（非 ttl 本身），``ttl=None`` 视为永不过期
  （剩余时间无穷大：排序最优先、筛选恒通过）；
- 租赁无过期时间，仅可被显式 ``release/remove/release_all`` 解除；
- 所有变更在 ``asyncio.Lock`` 下进行，保证并发安全与分配原子性。
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from enum import StrEnum

from ip_pool_common.models import Level2Record, Protocol


class AcquireStrategy(StrEnum):
    """提取策略：``latest`` 最新优先 / ``random`` 随机 /
    ``latency_asc`` 延迟从低到高 / ``remaining_desc`` 剩余时间从高到低。"""

    LATEST = "latest"
    RANDOM = "random"
    LATENCY_ASC = "latency_asc"
    REMAINING_DESC = "remaining_desc"


def remaining_seconds(rec: Level2Record, now: float) -> float:
    """剩余存活秒数：``created_at + ttl - now``；``ttl=None`` 永不过期 → ``inf``。"""
    if rec.ttl is None:
        return float("inf")
    return rec.created_at + rec.ttl - now


def _eligible(
    rec: Level2Record,
    now: float,
    max_latency_ms: float | None,
    min_remaining_sec: float | None,
) -> bool:
    """空闲且通过筛选：``latency_ms <= max_latency_ms`` 且剩余时间 >= ``min_remaining_sec``。"""
    if rec.leased:
        return False
    if max_latency_ms is not None and rec.latency_ms > max_latency_ms:
        return False
    if min_remaining_sec is not None and remaining_seconds(rec, now) < min_remaining_sec:
        return False
    return True


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

    async def acquire(
        self,
        strategy: AcquireStrategy | str = AcquireStrategy.LATEST,
        *,
        max_latency_ms: float | None = None,
        min_remaining_sec: float | None = None,
        now: float | None = None,
    ) -> Level2Record | None:
        """按策略租赁一条空闲记录并原子标记租赁；无候选返回 None。

        - 先按 ``max_latency_ms``（延迟上限）与 ``min_remaining_sec``（剩余时间下限，
          默认均不筛选）过滤出空闲候选集；
        - ``latest``：入池顺序尾部向前首个候选（默认，兼容旧行为）；
        - ``random``：候选集均匀随机取一；
        - ``latency_asc`` / ``remaining_desc``：单次提取不排序，一次扫描取
          argmin/argmax（并列取先入池者）；
        - 无候选（空池/全被租赁/全被筛选）返回 None。
        """
        async with self._lock:
            ts = time.time() if now is None else now
            strat = AcquireStrategy(strategy)
            candidates = [
                rec
                for rec in (self._records[key] for key in self._order)
                if _eligible(rec, ts, max_latency_ms, min_remaining_sec)
            ]
            if not candidates:
                return None
            if strat is AcquireStrategy.LATEST:
                chosen = candidates[-1]
            elif strat is AcquireStrategy.RANDOM:
                chosen = random.choice(candidates)
            elif strat is AcquireStrategy.LATENCY_ASC:
                chosen = min(candidates, key=lambda r: r.latency_ms)
            else:  # REMAINING_DESC
                chosen = max(candidates, key=lambda r: remaining_seconds(r, ts))
            chosen.leased = True
            chosen.leased_at = ts
            return chosen

    async def acquire_batch(
        self,
        count: int,
        strategy: AcquireStrategy | str = AcquireStrategy.LATEST,
        *,
        max_latency_ms: float | None = None,
        min_remaining_sec: float | None = None,
        now: float | None = None,
    ) -> list[Level2Record]:
        """按策略单锁内原子租赁至多 ``count`` 条空闲记录，按选取顺序返回。

        - 空闲不足 ``count`` 时返回能租到的全部（部分满足）；无候选返回 ``[]``；
        - 选取顺序：``latest`` 最新在前 / ``latency_asc`` 延迟升序 /
          ``remaining_desc`` 剩余时间降序（并列保持先入池在前）/ ``random`` 随机；
        - ``count <= 0`` 抛 ``ValueError``（HTTP 层负责参数校验）。
        """
        if count <= 0:
            raise ValueError(f"count must be positive, got {count}")
        async with self._lock:
            ts = time.time() if now is None else now
            strat = AcquireStrategy(strategy)
            candidates = [
                rec
                for rec in (self._records[key] for key in self._order)
                if _eligible(rec, ts, max_latency_ms, min_remaining_sec)
            ]
            take = min(count, len(candidates))
            if take == 0:
                return []
            if strat is AcquireStrategy.LATEST:
                selected = list(reversed(candidates))[:take]
            elif strat is AcquireStrategy.RANDOM:
                selected = random.sample(candidates, take)
            elif strat is AcquireStrategy.LATENCY_ASC:
                selected = sorted(candidates, key=lambda r: r.latency_ms)[:take]
            else:  # REMAINING_DESC
                selected = sorted(
                    candidates, key=lambda r: -remaining_seconds(r, ts)
                )[:take]
            for rec in selected:
                rec.leased = True
                rec.leased_at = ts
            return selected

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
