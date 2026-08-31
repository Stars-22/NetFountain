"""pool.py 测试：环形淘汰 / TTL 清扫 / 协议计数 / after(id) / 并发安全。"""
from __future__ import annotations

import asyncio

import pytest

from app.pool import Level1Pool
from ip_pool_common.models import Protocol


async def test_add_assigns_incrementing_unique_ids(make_ip):
    pool = Level1Pool(max_size=100)
    now = 1000.0
    ids = []
    for i in range(5):
        rec = await pool.add(make_ip(i + 1), now)
        ids.append(rec.id)
    assert ids == [0, 1, 2, 3, 4]
    assert pool.next_id == 5
    rec = await pool.add(make_ip(99), now)
    assert rec.id == 5
    assert len({r.id for r in await pool.all()}) == 6


async def test_ring_eviction_keeps_pool_at_capacity(make_ip):
    pool = Level1Pool(max_size=3)
    now = 1000.0
    for i in range(6):
        await pool.add(make_ip(i + 1), now)
    assert pool.size() == 3
    remaining = await pool.all()
    assert [r.id for r in remaining] == [3, 4, 5]
    assert [r.ip for r in remaining] == ["10.0.0.4", "10.0.0.5", "10.0.0.6"]
    assert pool.next_id == 6


async def test_protocol_counts_increment(make_ip):
    pool = Level1Pool(max_size=100)
    now = 1000.0
    await pool.add(make_ip(1, Protocol.HTTP), now)
    await pool.add(make_ip(2, Protocol.HTTPS), now)
    await pool.add(make_ip(3, Protocol.SOCKS4), now)
    await pool.add(make_ip(4, Protocol.SOCKS5), now)
    await pool.add(make_ip(5, Protocol.HTTP), now)
    counts = pool.counts()
    assert counts.total == 5
    assert counts.http == 2
    assert counts.https == 1
    assert counts.socks4 == 1
    assert counts.socks5 == 1


async def test_eviction_decrements_protocol_count(make_ip):
    pool = Level1Pool(max_size=2)
    now = 1000.0
    await pool.add(make_ip(1, Protocol.HTTP), now)
    await pool.add(make_ip(2, Protocol.HTTP), now)
    await pool.add(make_ip(3, Protocol.SOCKS5), now)
    counts = pool.counts()
    assert counts.total == 2
    assert counts.http == 1
    assert counts.socks5 == 1


async def test_ttl_sweep_removes_only_expired(make_ip):
    pool = Level1Pool(max_size=100)
    now = 1000.0
    await pool.add(make_ip(1, ttl=10), now)      # expires at 1010
    await pool.add(make_ip(2, ttl=100), now)     # expires at 1100
    await pool.add(make_ip(3, ttl=None), now)    # never expires
    removed = await pool.sweep_ttl(1005.0)
    assert removed == 0
    removed = await pool.sweep_ttl(1010.5)
    assert removed == 1
    remaining = {r.id for r in await pool.all()}
    assert remaining == {1, 2}
    removed = await pool.sweep_ttl(1100.5)
    assert removed == 1
    remaining = {r.id for r in await pool.all()}
    assert remaining == {2}


async def test_ttl_none_never_swept(make_ip):
    pool = Level1Pool(max_size=100)
    now = 1000.0
    await pool.add(make_ip(1, ttl=None), now)
    removed = await pool.sweep_ttl(now + 10_000.0)
    assert removed == 0
    assert pool.size() == 1


async def test_after_boundary(make_ip):
    pool = Level1Pool(max_size=100)
    now = 1000.0
    for i in range(5):
        await pool.add(make_ip(i + 1), now)
    assert [r.id for r in await pool.after(2)] == [3, 4]
    assert [r.id for r in await pool.after(4)] == []
    assert [r.id for r in await pool.after(999)] == []
    assert [r.id for r in await pool.after(-1)] == [0, 1, 2, 3, 4]
    assert await pool.after(0) != []
    empty = Level1Pool(max_size=100)
    assert await empty.after(0) == []


async def test_max_id_reflects_current_present_records(make_ip):
    pool = Level1Pool(max_size=100)
    now = 1000.0
    assert pool.max_id is None
    for i in range(3):
        await pool.add(make_ip(i + 1), now)
    assert pool.max_id == 2


async def test_max_id_updates_after_eviction_and_ttl_sweep(make_ip):
    pool = Level1Pool(max_size=3)
    now = 1000.0
    for i in range(6):
        await pool.add(make_ip(i + 1), now)
    assert [r.id for r in await pool.all()] == [3, 4, 5]
    assert pool.max_id == 5
    await pool.add(make_ip(7, ttl=10), now)
    assert pool.max_id == 6
    assert await pool.sweep_ttl(now + 11) == 1
    assert pool.max_id == 5


async def test_all_preserves_insertion_order(make_ip):
    pool = Level1Pool(max_size=100)
    now = 1000.0
    for i in range(5):
        await pool.add(make_ip(i + 1), now)
    records = await pool.all()
    assert [r.id for r in records] == [0, 1, 2, 3, 4]
    assert [r.ip for r in records] == [f"10.0.0.{i}" for i in range(1, 6)]


async def test_concurrent_add_no_loss_unique_ids(make_ip):
    pool = Level1Pool(max_size=500)
    now = 1000.0
    total = 200

    async def _add(i: int):
        await pool.add(make_ip(i), now)

    await asyncio.gather(*(_add(i) for i in range(total)))
    records = await pool.all()
    ids = [r.id for r in records]
    assert len(ids) == total
    assert len(set(ids)) == total
    assert pool.size() == total
    assert pool.counts().total == pool.size()


async def test_concurrent_add_respects_capacity(make_ip):
    pool = Level1Pool(max_size=10)
    now = 1000.0
    total = 100

    async def _add(i: int):
        await pool.add(make_ip(i), now)

    await asyncio.gather(*(_add(i) for i in range(total)))
    assert pool.size() == 10
    records = await pool.all()
    assert [r.id for r in records] == list(range(90, 100))
    assert pool.counts().total == 10


async def test_size_and_counts_consistent_after_random_ops(make_ip):
    pool = Level1Pool(max_size=5)
    now = 1000.0
    for i in range(12):
        proto = [Protocol.HTTP, Protocol.HTTPS, Protocol.SOCKS4, Protocol.SOCKS5][i % 4]
        await pool.add(make_ip(i + 1, protocol=proto), now)
        await pool.sweep_ttl(now + (i * 0.001))
        counts = pool.counts()
        assert pool.size() == counts.total


async def test_record_fields_on_add(make_ip):
    pool = Level1Pool(max_size=100)
    now = 1234.5
    rec = await pool.add(make_ip(7, Protocol.HTTPS, region="CN", ttl=60), now)
    assert rec.id == 0
    assert rec.ip == "10.0.0.7"
    assert rec.port == 8007
    assert rec.protocol == Protocol.HTTPS
    assert rec.proxy_url == "https://10.0.0.7:8007"
    assert rec.region == "CN"
    assert rec.ttl == 60
    assert rec.created_at == now
    assert rec.last_verified_at == now


def test_max_size_validation():
    with pytest.raises(ValueError):
        Level1Pool(max_size=0)
    with pytest.raises(ValueError):
        Level1Pool(max_size=-5)


async def test_add_dedup_same_proxy_url_new_id(make_ip):
    pool = Level1Pool(max_size=100)
    now = 1000.0
    first = await pool.add(make_ip(1, ttl=10), now)
    second = await pool.add(make_ip(1, ttl=20), now + 5)
    assert first.id == 0
    assert second.id == 1
    assert pool.size() == 1
    assert pool.next_id == 2
    assert pool.duplicates == 1
    assert pool.counts().total == 1


async def test_add_dedup_refreshes_fields_at_tail(make_ip):
    pool = Level1Pool(max_size=100)
    now = 1000.0
    await pool.add(make_ip(1, region="old", ttl=10), now)
    await pool.add(make_ip(2, region="keep", ttl=100), now)
    rec = await pool.add(
        make_ip(1, region="new", ttl=60), now + 5
    )
    assert rec.id == 2
    assert rec.region == "new"
    assert rec.ttl == 60
    assert rec.created_at == now + 5
    assert rec.last_verified_at == now + 5
    records = await pool.all()
    assert [r.id for r in records] == [1, 2]
    assert [r.ip for r in records] == ["10.0.0.2", "10.0.0.1"]
    assert pool.duplicates == 1


async def test_add_same_endpoint_different_protocol_distinct(make_ip):
    pool = Level1Pool(max_size=100)
    now = 1000.0
    a = await pool.add(make_ip(1, Protocol.HTTP), now)
    b = await pool.add(make_ip(1, Protocol.SOCKS5), now)
    assert a.id != b.id
    assert pool.size() == 2
    assert pool.duplicates == 0
    assert {r.proxy_url for r in await pool.all()} == {
        "http://10.0.0.1:8001",
        "socks5://10.0.0.1:8001",
    }


async def test_add_dedup_after_eviction_reenters(make_ip):
    pool = Level1Pool(max_size=2)
    now = 1000.0
    await pool.add(make_ip(1), now)      # id 0, evicted next
    await pool.add(make_ip(2), now)      # id 1, evicted next
    await pool.add(make_ip(3), now)      # evicts id 0
    assert pool.size() == 2
    rec = await pool.add(make_ip(1), now + 5)
    assert rec.id == 3
    assert pool.size() == 2
    assert pool.duplicates == 0
    assert {r.ip for r in await pool.all()} == {"10.0.0.1", "10.0.0.3"}


async def test_ttl_sweep_cleans_key_index(make_ip):
    pool = Level1Pool(max_size=100)
    now = 1000.0
    await pool.add(make_ip(1, ttl=10), now)
    assert await pool.sweep_ttl(now + 11) == 1
    rec = await pool.add(make_ip(1, ttl=30), now + 11)
    assert rec.id == 1
    assert pool.size() == 1
    assert pool.duplicates == 0


async def test_concurrent_add_same_endpoint_dedup(make_ip):
    pool = Level1Pool(max_size=500)
    now = 1000.0
    total = 50

    async def _add(_i: int):
        await pool.add(make_ip(1, ttl=60 + _i), now)

    await asyncio.gather(*(_add(i) for i in range(total)))
    records = await pool.all()
    assert len(records) == 1
    assert pool.size() == 1
    assert pool.counts().total == 1
    assert pool.duplicates == total - 1
    assert pool.next_id == total
