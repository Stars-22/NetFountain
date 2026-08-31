"""routes.py + main.py 测试：7 端点契约 / 空池错误码 / 不存在 id / 计数递增。

覆盖测试计划书 L2-RT-001 ~ 010。
"""
from __future__ import annotations

import time

from app.config import Level2Settings
from app.main import create_app
from app.pool import Level2Pool, ServiceStats
from ip_pool_common.models import Protocol


def _make_app(pool=None, stats=None, **kwargs):
    return create_app(
        Level2Settings(),
        pool=pool if pool is not None else Level2Pool(),
        stats=stats if stats is not None else ServiceStats(),
        start_tasks=False,
        **kwargs,
    )


async def _seed_pool(pool, make_l2, make_ip, n=5, leased_every=2):
    protos = [Protocol.HTTP, Protocol.HTTPS, Protocol.SOCKS4, Protocol.SOCKS5]
    for i in range(1, n + 1):
        rec = await pool.upsert(
            make_l2(
                make_ip(i, protocol=protos[(i - 1) % 4], ttl=120.0),
                latency=float(i * 100),
            )
        )
        if i % leased_every == 0:
            rec.leased = True
            rec.leased_at = 1100.0


async def test_status_fields_complete(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=5)
    stats = ServiceStats(total_pulled=100, total_entered=42, last_synced_id=7)
    app = _make_app(pool, stats)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["msg"] == "ok"
    data = body["data"]
    assert set(data) >= {
        "uptime",
        "total_pulled",
        "total_entered",
        "api_call_count",
        "last_synced_id",
        "pool_stats",
        "errors",
        "drops",
    }
    assert data["total_pulled"] == 100
    assert data["total_entered"] == 42
    assert data["last_synced_id"] == 7
    assert data["api_call_count"] >= 1
    assert data["errors"] == {
        "sync_failures": 0,
        "test_failures": 0,
        "revalidate_failures": 0,
        "ttl_sweep_failures": 0,
        "empty_acquires": 0,
    }
    assert data["drops"] == 0
    ps = data["pool_stats"]
    assert ps["total"] == 5
    assert ps["by_proto"] == {"http": 2, "https": 1, "socks4": 1, "socks5": 1}
    assert ps["leased_total"] == 2
    assert ps["leased_by_proto"] == {"https": 1, "socks5": 1}
    assert ps["free_total"] == 3
    assert ps["free_by_proto"] == {"http": 2, "socks4": 1}


async def test_status_uptime_uses_start_time(running_app):
    app = _make_app(start_time=time.time() - 10)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/status")
    assert 9.0 <= resp.json()["data"]["uptime"] <= 11.0


async def test_count_endpoint(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=5)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/count")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 5
    assert data["by_proto"] == {"http": 2, "https": 1, "socks4": 1, "socks5": 1}
    assert data["leased_total"] == 2
    assert data["leased_by_proto"] == {"https": 1, "socks5": 1}
    assert data["free_total"] == 3
    assert data["free_by_proto"] == {"http": 2, "socks4": 1}


async def test_ips_endpoint_full_fields_no_lease_mark(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=3)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/ips")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 3
    assert set(data[0]) == {
        "id",
        "ip",
        "port",
        "protocol",
        "proxy_url",
        "latency_ms",
        "leased",
        "ttl",
        "created_at",
    }
    assert data[0]["protocol"] == "http"
    assert data[0]["proxy_url"] == "http://10.0.0.1:8001"
    assert data[0]["latency_ms"] == 100.0
    assert data[0]["leased"] is False
    assert data[1]["leased"] is True  # i=2 已租赁，如实反映
    # GET /ips 不产生租赁标记
    assert pool.stats().leased_total == 1


async def test_acquire_returns_one_and_leases(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=3)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["leased"] is True
    assert data["id"] == 2  # 最新优先 → 最后入池的 10.0.0.3
    assert pool.stats().leased_total == 2  # 预置 1 条 + 本次获取 1 条


async def test_acquire_empty_pool_returns_emptypool(running_app):
    app = _make_app()
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 40402
    assert body["msg"]
    assert body["data"] is None


async def test_acquire_all_leased_returns_emptypool(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=2, leased_every=1)  # 全租
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire")
    assert resp.json()["code"] == 40402
    assert app.state.stats.empty_acquires == 1


async def test_empty_acquire_counted_in_status(running_app):
    app = _make_app()
    async with running_app(app) as client:
        await client.post("/api/v1/ips/acquire")
        await client.post("/api/v1/ips/acquire")
        resp = await client.get("/api/v1/status")
    assert resp.json()["data"]["errors"]["empty_acquires"] == 2


async def test_release_success(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=1)
    acquired = await pool.acquire()
    assert acquired is not None
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post(f"/api/v1/ips/{acquired.id}/release")
    assert resp.json()["code"] == 0
    assert resp.json()["data"] is True
    assert pool.stats().leased_total == 0


async def test_delete_success(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=1)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.delete("/api/v1/ips/0")
    assert resp.json()["code"] == 0
    assert pool.stats().total == 0


async def test_release_all(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=4, leased_every=2)
    assert pool.stats().leased_total == 2
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/release-all")
    assert resp.json()["code"] == 0
    assert resp.json()["data"] == 2
    assert pool.stats().leased_total == 0
    assert pool.stats().free_total == 4


async def test_release_nonexistent_returns_not_found(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=1)
    app = _make_app(pool)
    async with running_app(app) as client:
        r_release = await client.post("/api/v1/ips/999/release")
        r_delete = await client.delete("/api/v1/ips/999")
    assert r_release.json()["code"] == 40400
    assert r_release.json()["msg"]
    assert r_delete.json()["code"] == 40400
    assert pool.stats().total == 1


async def test_api_call_count_increments(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=1)
    app = _make_app(pool)
    async with running_app(app) as client:
        await client.get("/api/v1/count")
        await client.post("/api/v1/ips/acquire")
        resp = await client.get("/api/v1/status")
    assert resp.json()["data"]["api_call_count"] == 3


async def test_non_v1_requests_not_counted(running_app):
    app = _make_app()
    async with running_app(app) as client:
        await client.get("/openapi.json")
        await client.get("/docs")
        resp = await client.get("/api/v1/status")
    assert resp.json()["data"]["api_call_count"] == 1


async def test_release_then_acquire_again(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=1)
    app = _make_app(pool)
    async with running_app(app) as client:
        r1 = await client.post("/api/v1/ips/acquire")
        rec = r1.json()["data"]
        await client.post(f"/api/v1/ips/{rec['id']}/release")
        r2 = await client.post("/api/v1/ips/acquire")
    assert r2.json()["data"]["id"] == rec["id"]


def test_create_app_defaults_when_config_missing(monkeypatch, tmp_path):
    import app.main as main

    monkeypatch.setattr(main, "_CONFIG_PATH", str(tmp_path / "missing.yaml"))
    app = create_app()
    assert app.state.settings.site.name == "site_a"
    assert app.state.settings.service.port == 8001
    assert isinstance(app.state.pool, Level2Pool)
    assert app.state.stats.last_synced_id is None


def test_create_app_loads_config_when_present(monkeypatch):
    import os

    import app.main as main

    example = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "config", "level2_pool.example.yaml"
    )
    monkeypatch.setattr(main, "_CONFIG_PATH", example)
    app = create_app()
    assert app.state.settings.site.name == "site_a"
    assert app.state.settings.sync.interval == 3.0
    assert app.state.settings.test.latency_threshold_ms == 2000
    assert app.state.settings.revalidate_interval == 60.0