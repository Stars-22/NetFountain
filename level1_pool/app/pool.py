"""环形池：循环队列（deque maxlen）+ TTL 清扫 + 协议计数 + 按 proxy_url 去重。

- 容量淘汰由 ``deque(maxlen)`` 天然实现（满则弹出最旧），O(1)；
- TTL 淘汰为周期性主动清扫，二者独立，容量兜底 + TTL 精细化；
- 以 ``proxy_url``（ip+port+protocol）为去重键：重复入池时删除旧记录并以新 id
  重建（刷新 ttl/region，记录移至尾部，新 id 触发二级池增量同步）；
- 所有变更在 ``asyncio.Lock`` 下进行，保证并发安全；
- ``id`` 全局自增、绝不复用，作为二级池增量同步的水位线依据。
"""
from __future__ import annotations

import asyncio
from collections import Counter, deque
from dataclasses import dataclass

from ip_pool_common.models import IpRecord, Protocol, ProviderIp, build_proxy_url


@dataclass
class PoolCounts:
    """池内各协议计数快照。"""

    total: int = 0
    http: int = 0
    https: int = 0
    socks4: int = 0
    socks5: int = 0


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


class Level1Pool:
    """一级池：dict 索引 + 循环队列 + 协议计数。"""

    def __init__(self, max_size: int = 500):
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size = max_size
        self._records: dict[int, IpRecord] = {}
        self._order: deque[int] = deque(maxlen=max_size)
        self._counts: Counter = Counter()
        self._key_index: dict[str, int] = {}
        self._duplicates: int = 0
        self._next_id: int = 0
        self._lock = asyncio.Lock()

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def next_id(self) -> int:
        return self._next_id

    @property
    def duplicates(self) -> int:
        """因 proxy_url 重复被刷新重建（删除重建）的累计次数。"""
        return self._duplicates

    async def add(self, ip: ProviderIp, now: float) -> IpRecord:
        """按 ``proxy_url``（ip+port+protocol）去重入池。

        - 重复：删除旧记录并分配新 id 重建（刷新 ttl/region，移至尾部），
          ``duplicates`` 累计；新 id 会触发二级池增量同步；
        - 新记录：分配自增 id 入池；已满时弹出最旧记录（容量淘汰，同步清理去重索引）。
        """
        async with self._lock:
            key = build_proxy_url(ip.ip, ip.port, ip.protocol)
            old_id = self._key_index.pop(key, None)
            if old_id is not None:
                old = self._records.pop(old_id, None)
                if old is not None:
                    self._order.remove(old_id)
                    self._counts[old.protocol] -= 1
                self._duplicates += 1
            rec_id = self._next_id
            self._next_id += 1
            record = IpRecord(
                id=rec_id,
                ip=ip.ip,
                port=ip.port,
                protocol=ip.protocol,
                proxy_url=key,
                region=ip.region,
                ttl=ip.ttl,
                created_at=now,
                last_verified_at=now,
            )
            if len(self._order) == self._max_size:
                evicted_id = self._order.popleft()
                evicted = self._records.pop(evicted_id, None)
                if evicted is not None:
                    self._counts[evicted.protocol] -= 1
                    self._key_index.pop(evicted.proxy_url, None)
            self._records[rec_id] = record
            self._order.append(rec_id)
            self._counts[record.protocol] += 1
            self._key_index[key] = rec_id
            return record

    async def sweep_ttl(self, now: float) -> int:
        """删除 ``created_at + ttl < now`` 的过期记录（ttl 为 None 永不过期）。

        返回删除数量。
        """
        async with self._lock:
            expired = [
                rec_id
                for rec_id, rec in self._records.items()
                if rec.ttl is not None and rec.created_at + rec.ttl < now
            ]
            for rec_id in expired:
                rec = self._records.pop(rec_id, None)
                if rec is not None:
                    self._order.remove(rec_id)
                    self._counts[rec.protocol] -= 1
                    self._key_index.pop(rec.proxy_url, None)
            return len(expired)

    def size(self) -> int:
        return len(self._records)

    def counts(self) -> PoolCounts:
        return PoolCounts(
            total=len(self._records),
            http=self._counts[Protocol.HTTP],
            https=self._counts[Protocol.HTTPS],
            socks4=self._counts[Protocol.SOCKS4],
            socks5=self._counts[Protocol.SOCKS5],
        )

    async def all(self) -> list[IpRecord]:
        """按入池顺序返回全部记录。"""
        async with self._lock:
            return [self._records[i] for i in self._order]

    async def after(self, id_: int) -> list[IpRecord]:
        """返回 ``id > id_`` 的全部记录（按入池顺序）；越界返回空列表。"""
        async with self._lock:
            return [self._records[i] for i in self._order if i > id_]
