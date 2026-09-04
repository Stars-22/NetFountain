"""pool.py 测试：upsert 去重 / 本地 id / 租赁语义 / 原子分配 / 计数 / TTL 淘汰 /
提取策略与筛选 / 批量提取。

覆盖测试计划书 L2-POOL-001 ~ 013 及提取策略扩展用例。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.pool import AcquireStrategy
from ip_pool_common.models import Level2Record, Protocol


def _l2(make_l2, make_ip, idx, protocol=Protocol.HTTP, latency=10.0, **kwargs) -> Level2Record:
    return make_l2(make_ip(idx, protocol=protocol), latency=latency, **kwargs)


async def test_upsert_dedup_by_proxy_url(pool, make_l2, make_ip):
    rec1 = _l2(make_l2, make_ip, 1, latency=10.0)
    returned1 = await pool.upsert(rec1)
    assert pool.stats().total == 1

    rec2 = _l2(make_l2, make_ip, 1, latency=2500.0, created_at=2000.0)
    returned2 = await pool.upsert(rec2)
    assert pool.stats().total == 1
    assert returned2.id == returned1.id
    assert returned2.latency_ms == 2500.0
    assert returned2.created_at == 2000.0


async def test_upsert_keeps_leased_state(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1))
    acquired = await pool.acquire()
    assert acquired is not None and acquired.leased

    refreshed = await pool.upsert(_l2(make_l2, make_ip, 1, latency=99.0))
    assert refreshed.id == acquired.id
    assert refreshed.leased is True
    assert refreshed.leased_at == acquired.leased_at


async def test_local_id_monotonic_no_reuse(pool, make_l2, make_ip):
    ids = []
    for i in range(1, 5):
        rec = await pool.upsert(_l2(make_l2, make_ip, i))
        ids.append(rec.id)
    assert ids == [0, 1, 2, 3]

    removed = await pool.remove(1)
    assert removed is True
    new = await pool.upsert(_l2(make_l2, make_ip, 9))
    assert new.id == 4
    assert new.id not in ids


async def test_acquire_newest_first(pool, make_l2, make_ip):
    for i in range(1, 4):
        await pool.upsert(_l2(make_l2, make_ip, i))
    r1 = await pool.acquire()
    r2 = await pool.acquire()
    r3 = await pool.acquire()
    r4 = await pool.acquire()
    assert [r1.ip, r2.ip, r3.ip] == ["10.0.0.3", "10.0.0.2", "10.0.0.1"]
    assert r4 is None
    assert all(r.leased for r in (r1, r2, r3))


async def test_acquire_skips_leased(pool, make_l2, make_ip):
    for i in range(1, 4):
        await pool.upsert(_l2(make_l2, make_ip, i))
    newest = await pool.acquire()
    assert newest.ip == "10.0.0.3"
    second = await pool.acquire()
    assert second.ip == "10.0.0.2"
    third = await pool.acquire()
    assert third.ip == "10.0.0.1"
    assert await pool.acquire() is None


async def test_lease_has_no_expiry(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1))
    rec = await pool.acquire()
    assert rec is not None and rec.leased is True
    leased_at = rec.leased_at
    await asyncio.sleep(0.02)
    assert await pool.acquire() is None
    assert rec.leased is True
    assert rec.leased_at == leased_at


async def test_acquire_atomic_100_concurrent(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1))
    results = await asyncio.gather(*(pool.acquire() for _ in range(100)))
    successes = [r for r in results if r is not None]
    assert len(successes) == 1
    assert successes[0].leased is True
    assert all(r is None for r in results[1:])
    assert pool.stats().leased_total == 1


async def test_release(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1))
    rec = await pool.acquire()
    assert rec is not None
    released = await pool.release(rec.id)
    assert released is True
    assert rec.leased is False
    assert rec.leased_at is None
    again = await pool.acquire()
    assert again is not None and again.id == rec.id


async def test_release_of_free_record_ok(pool, make_l2, make_ip):
    rec = await pool.upsert(_l2(make_l2, make_ip, 1))
    assert await pool.release(rec.id) is True
    assert rec.leased is False


async def test_remove(pool, make_l2, make_ip):
    rec = await pool.upsert(_l2(make_l2, make_ip, 1))
    assert await pool.remove(rec.id) is True
    assert pool.stats().total == 0
    assert await pool.acquire() is None


async def test_remove_leased(pool, make_l2, make_ip):
    rec = await pool.upsert(_l2(make_l2, make_ip, 1))
    await pool.acquire()
    assert rec.leased is True
    assert await pool.remove(rec.id) is True
    assert pool.stats().total == 0


async def test_release_all(pool, make_l2, make_ip):
    for i in range(1, 4):
        await pool.upsert(_l2(make_l2, make_ip, i))
    acquired = []
    for _ in range(3):
        rec = await pool.acquire()
        assert rec is not None
        acquired.append(rec)
    assert pool.stats().leased_total == 3
    count = await pool.release_all()
    assert count == 3
    assert pool.stats().leased_total == 0
    assert all(rec.leased is False for rec in pool.all())


async def test_release_all_none_leased(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1))
    assert await pool.release_all() == 0


async def test_invalid_id_returns_false(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1))
    assert await pool.release(999) is False
    assert await pool.remove(999) is False
    assert pool.stats().total == 1


async def test_stats_breakdown(mixed_pool):
    stats = mixed_pool.stats()
    assert stats.total == 5
    assert stats.by_proto == {
        Protocol.HTTP: 2,
        Protocol.HTTPS: 1,
        Protocol.SOCKS4: 1,
        Protocol.SOCKS5: 1,
    }
    assert stats.leased_total == 2
    assert stats.leased_by_proto == {Protocol.HTTPS: 1, Protocol.SOCKS5: 1}
    assert stats.free_total == 3
    assert stats.free_by_proto == {
        Protocol.HTTP: 2,
        Protocol.SOCKS4: 1,
    }


async def test_stats_empty_pool(pool):
    stats = pool.stats()
    assert stats.total == 0
    assert stats.by_proto == {}
    assert stats.leased_total == 0
    assert stats.free_total == 0


async def test_all_does_not_change_state(mixed_pool):
    before = mixed_pool.stats()
    snapshot1 = mixed_pool.all()
    snapshot2 = mixed_pool.all()
    after = mixed_pool.stats()
    assert len(snapshot1) == len(snapshot2) == before.total
    assert [r.leased for r in snapshot2] == [r.leased for r in snapshot1]
    assert after.total == before.total
    assert after.leased_total == before.leased_total
    assert all(
        r1.leased == r2.leased for r1, r2 in zip(snapshot1, snapshot2)
    )


async def test_all_preserves_order(pool, make_l2, make_ip):
    for i in range(1, 4):
        await pool.upsert(_l2(make_l2, make_ip, i))
    assert [r.ip for r in pool.all()] == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]


async def test_sweep_ttl_removes_only_expired(pool, make_l2, make_ip):
    now = time.time()
    await pool.upsert(
        make_l2(make_ip(1, ttl=5.0), created_at=now - 10.0)   # 过期
    )
    await pool.upsert(
        make_l2(make_ip(2, ttl=1000.0), created_at=now - 10.0)  # 未过期
    )
    await pool.upsert(
        make_l2(make_ip(3, ttl=None), created_at=now - 10.0)   # 永不
    )
    removed = await pool.sweep_ttl(now)
    assert removed == 1
    remaining = pool.all()
    assert [r.ip for r in remaining] == ["10.0.0.2", "10.0.0.3"]


async def test_sweep_ttl_removes_leased_expired(pool, make_l2, make_ip):
    now = time.time()
    await pool.upsert(make_l2(make_ip(1, ttl=5.0), created_at=now - 10.0))
    rec = await pool.acquire()
    assert rec is not None and rec.leased
    assert await pool.sweep_ttl(now) == 1
    assert pool.stats().total == 0


async def test_sweep_ttl_nothing_expired(pool, make_l2, make_ip):
    now = time.time()
    await pool.upsert(make_l2(make_ip(1, ttl=1000.0), created_at=now))
    assert await pool.sweep_ttl(now) == 0
    assert pool.stats().total == 1


async def test_sweep_ttl_after_upsert_refresh(pool, make_l2, make_ip):
    """upsert 刷新 created_at 后，TTL 基准随之刷新，过期判定不误删。"""
    now = time.time()
    await pool.upsert(make_l2(make_ip(1, ttl=5.0), created_at=now - 10.0))
    await pool.sweep_ttl(now - 1.0)  # 恰好仍过期
    refreshed = await pool.upsert(
        make_l2(make_ip(1, ttl=5.0), created_at=now)
    )
    assert refreshed.created_at == now
    assert await pool.sweep_ttl(now) == 0
    assert pool.stats().total == 1


async def test_acquire_release_roundtrip_stats(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1))
    rec = await pool.acquire()
    assert pool.stats().leased_total == 1
    await pool.release(rec.id)
    assert pool.stats().leased_total == 0
    assert pool.stats().free_total == 1


async def test_remove_nonexistent_after_remove(pool, make_l2, make_ip):
    rec = await pool.upsert(_l2(make_l2, make_ip, 1))
    assert await pool.remove(rec.id) is True
    assert await pool.remove(rec.id) is False
    assert await pool.release(rec.id) is False


async def test_next_id_never_reused_after_remove(pool, make_l2, make_ip):
    first = await pool.upsert(_l2(make_l2, make_ip, 1))
    await pool.upsert(_l2(make_l2, make_ip, 2))
    await pool.remove(first.id)
    assert pool.next_id == 2
    third = await pool.upsert(_l2(make_l2, make_ip, 3))
    assert third.id == 2


# ---------------------------------------------------------------------------
# 提取策略（单条提取：不排序，直接 argmin/argmax）
# ---------------------------------------------------------------------------


async def test_acquire_default_and_explicit_latest(pool, make_l2, make_ip):
    """默认与显式 latest 均为最新优先（旧行为回归）。"""
    for i in range(1, 4):
        await pool.upsert(_l2(make_l2, make_ip, i))
    r1 = await pool.acquire()
    r2 = await pool.acquire(AcquireStrategy.LATEST)
    r3 = await pool.acquire("latest")
    assert [r.ip for r in (r1, r2, r3)] == ["10.0.0.3", "10.0.0.2", "10.0.0.1"]


async def test_acquire_strategy_latency_asc(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1, latency=300.0))
    await pool.upsert(_l2(make_l2, make_ip, 2, latency=100.0))
    await pool.upsert(_l2(make_l2, make_ip, 3, latency=200.0))
    rec = await pool.acquire(AcquireStrategy.LATENCY_ASC)
    assert rec.ip == "10.0.0.2"
    assert rec.leased is True
    second = await pool.acquire(AcquireStrategy.LATENCY_ASC)
    assert second.ip == "10.0.0.3"


async def test_acquire_latency_tie_takes_first_inserted(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1, latency=100.0))
    await pool.upsert(_l2(make_l2, make_ip, 2, latency=100.0))
    rec = await pool.acquire(AcquireStrategy.LATENCY_ASC)
    assert rec.ip == "10.0.0.1"


async def test_acquire_strategy_remaining_desc(pool, make_l2, make_ip):
    # 剩余时间 = created_at + ttl - now（now=1000）：-50 / 100 / 900
    await pool.upsert(make_l2(make_ip(1, ttl=50.0), created_at=1000.0))
    await pool.upsert(make_l2(make_ip(2, ttl=100.0), created_at=1000.0))
    await pool.upsert(make_l2(make_ip(3, ttl=1000.0), created_at=1000.0))
    rec = await pool.acquire(AcquireStrategy.REMAINING_DESC, now=1000.0)
    assert rec.ip == "10.0.0.3"
    second = await pool.acquire(AcquireStrategy.REMAINING_DESC, now=1000.0)
    assert second.ip == "10.0.0.2"


async def test_acquire_remaining_tie_takes_first_inserted(pool, make_l2, make_ip):
    await pool.upsert(make_l2(make_ip(1, ttl=100.0), created_at=1000.0))
    await pool.upsert(make_l2(make_ip(2, ttl=100.0), created_at=1000.0))
    rec = await pool.acquire(AcquireStrategy.REMAINING_DESC, now=1000.0)
    assert rec.ip == "10.0.0.1"


async def test_acquire_remaining_desc_ttl_none_is_infinity(pool, make_l2, make_ip):
    """ttl=None 永不过期 → 剩余时间无穷大，排序最优先。"""
    await pool.upsert(make_l2(make_ip(1, ttl=1e9), created_at=1000.0))
    await pool.upsert(make_l2(make_ip(2, ttl=None), created_at=1000.0))
    rec = await pool.acquire(AcquireStrategy.REMAINING_DESC, now=1000.0)
    assert rec.ip == "10.0.0.2"


async def test_acquire_strategy_random_leases_unique(pool, make_l2, make_ip):
    for i in range(1, 6):
        await pool.upsert(_l2(make_l2, make_ip, i))
    got = []
    for _ in range(5):
        rec = await pool.acquire(AcquireStrategy.RANDOM)
        assert rec is not None and rec.leased is True
        got.append(rec.ip)
    assert len(set(got)) == 5
    assert await pool.acquire(AcquireStrategy.RANDOM) is None


async def test_acquire_leased_at_uses_now_param(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1))
    rec = await pool.acquire(now=1234.5)
    assert rec.leased_at == 1234.5


# ---------------------------------------------------------------------------
# 提取筛选：max_latency_ms（延迟上限）/ min_remaining_sec（剩余时间下限）
# ---------------------------------------------------------------------------


async def test_acquire_filter_max_latency_ms(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1, latency=100.0))
    await pool.upsert(_l2(make_l2, make_ip, 2, latency=500.0))
    await pool.upsert(_l2(make_l2, make_ip, 3, latency=200.0))
    # latest + 筛选：尾向前首个延迟达标者
    rec = await pool.acquire(max_latency_ms=250.0)
    assert rec.ip == "10.0.0.3"
    rec2 = await pool.acquire(AcquireStrategy.LATENCY_ASC, max_latency_ms=150.0)
    assert rec2.ip == "10.0.0.1"


async def test_acquire_filter_max_latency_ms_excludes_all(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1, latency=100.0))
    assert await pool.acquire(max_latency_ms=50.0) is None
    assert pool.stats().leased_total == 0


async def test_acquire_filter_min_remaining_sec(pool, make_l2, make_ip):
    # 剩余时间（now=1000）：100 / 1000 / inf
    await pool.upsert(make_l2(make_ip(1, ttl=100.0), created_at=1000.0))
    await pool.upsert(make_l2(make_ip(2, ttl=1000.0), created_at=1000.0))
    await pool.upsert(make_l2(make_ip(3, ttl=None), created_at=1000.0))
    rec = await pool.acquire(min_remaining_sec=500.0, now=1000.0)
    assert rec.ip == "10.0.0.3"  # 最新且 ttl=None 恒通过
    rec2 = await pool.acquire(AcquireStrategy.REMAINING_DESC, min_remaining_sec=500.0, now=1000.0)
    assert rec2.ip == "10.0.0.2"


async def test_acquire_filter_min_remaining_excludes_expired_unswept(
    pool, make_l2, make_ip
):
    """未及清扫的已过期记录（剩余 < 0）被 min_remaining_sec=0 排除；剩余恰为 0 仍通过。"""
    await pool.upsert(make_l2(make_ip(1, ttl=50.0), created_at=1000.0))
    assert await pool.acquire(min_remaining_sec=0.0, now=1100.0) is None
    rec = await pool.acquire(min_remaining_sec=0.0, now=1050.0)
    assert rec is not None and rec.ip == "10.0.0.1"


async def test_acquire_skips_leased_under_filters(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1, latency=100.0))
    await pool.upsert(_l2(make_l2, make_ip, 2, latency=500.0))
    leased = await pool.acquire(max_latency_ms=600.0)  # 租走最新的 10.0.0.2
    assert leased.ip == "10.0.0.2"
    rec = await pool.acquire(AcquireStrategy.LATENCY_ASC, max_latency_ms=600.0)
    assert rec.ip == "10.0.0.1"


# ---------------------------------------------------------------------------
# 批量提取 acquire_batch
# ---------------------------------------------------------------------------


async def test_acquire_batch_latest_order(pool, make_l2, make_ip):
    for i in range(1, 6):
        await pool.upsert(_l2(make_l2, make_ip, i))
    batch = await pool.acquire_batch(3)
    assert [r.ip for r in batch] == ["10.0.0.5", "10.0.0.4", "10.0.0.3"]
    assert all(r.leased for r in batch)
    assert pool.stats().leased_total == 3


async def test_acquire_batch_latency_asc_sorted(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1, latency=300.0))
    await pool.upsert(_l2(make_l2, make_ip, 2, latency=100.0))
    await pool.upsert(_l2(make_l2, make_ip, 3, latency=200.0))
    await pool.upsert(_l2(make_l2, make_ip, 4, latency=50.0))
    batch = await pool.acquire_batch(2, AcquireStrategy.LATENCY_ASC)
    assert [(r.ip, r.latency_ms) for r in batch] == [
        ("10.0.0.4", 50.0),
        ("10.0.0.2", 100.0),
    ]


async def test_acquire_batch_remaining_desc_sorted(pool, make_l2, make_ip):
    await pool.upsert(make_l2(make_ip(1, ttl=100.0), created_at=1000.0))
    await pool.upsert(make_l2(make_ip(2, ttl=None), created_at=1000.0))
    await pool.upsert(make_l2(make_ip(3, ttl=1000.0), created_at=1000.0))
    batch = await pool.acquire_batch(3, AcquireStrategy.REMAINING_DESC, now=1000.0)
    assert [r.ip for r in batch] == ["10.0.0.2", "10.0.0.3", "10.0.0.1"]


async def test_acquire_batch_random_unique(pool, make_l2, make_ip):
    for i in range(1, 6):
        await pool.upsert(_l2(make_l2, make_ip, i))
    batch = await pool.acquire_batch(10, AcquireStrategy.RANDOM)
    assert len(batch) == 5
    assert len({r.ip for r in batch}) == 5
    assert pool.stats().leased_total == 5


async def test_acquire_batch_partial_then_empty(pool, make_l2, make_ip):
    for i in range(1, 4):
        await pool.upsert(_l2(make_l2, make_ip, i))
    batch = await pool.acquire_batch(10)
    assert len(batch) == 3
    assert await pool.acquire_batch(10) == []
    await pool.upsert(_l2(make_l2, make_ip, 9))
    again = await pool.acquire_batch(10)
    assert [r.ip for r in again] == ["10.0.0.9"]


async def test_acquire_batch_with_filters(pool, make_l2, make_ip):
    for i, lat in [(1, 100.0), (2, 500.0), (3, 200.0), (4, 50.0)]:
        await pool.upsert(_l2(make_l2, make_ip, i, latency=lat))
    batch = await pool.acquire_batch(5, max_latency_ms=250.0)
    assert [r.ip for r in batch] == ["10.0.0.4", "10.0.0.3", "10.0.0.1"]


async def test_acquire_batch_invalid_count_raises(pool, make_l2, make_ip):
    with pytest.raises(ValueError):
        await pool.acquire_batch(0)
    with pytest.raises(ValueError):
        await pool.acquire_batch(-1)
    assert pool.stats().total == 0


async def test_acquire_batch_string_strategy_accepted(pool, make_l2, make_ip):
    await pool.upsert(_l2(make_l2, make_ip, 1, latency=200.0))
    await pool.upsert(_l2(make_l2, make_ip, 2, latency=100.0))
    batch = await pool.acquire_batch(1, "latency_asc")
    assert batch[0].ip == "10.0.0.2"


async def test_acquire_batch_atomic_concurrent(pool, make_l2, make_ip):
    """100 并发批量提取（各 count=2）池容量 3：无重复租赁、总数恰为 3。"""
    for i in range(1, 4):
        await pool.upsert(_l2(make_l2, make_ip, i))
    results = await asyncio.gather(*(pool.acquire_batch(2) for _ in range(100)))
    leased = [rec for batch in results for rec in batch]
    assert len(leased) == 3
    assert len({rec.id for rec in leased}) == 3
    assert pool.stats().leased_total == 3
    assert all(rec.leased for rec in leased)