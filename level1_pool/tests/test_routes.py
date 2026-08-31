"""routes.py + main.py 测试：4 端点字段完整 / after 越界 / 空池 / 计数递增。"""
from __future__ import annotations

import time

from app.config import Level1Settings
from app.main import create_app
from app.pool import Level1Pool, ServiceStats
from ip_pool_common.models import Protocol, ProviderIp

_PROTOCOLS = [Protocol.HTTP, Protocol.HTTPS, Protocol.SOCKS4, Protocol.SOCKS5]


async def _seed_pool(pool, n=5):
    now = 1000.0
    for i in range(n):
        await pool.add(
            ProviderIp(
                ip=f"1.2.3.{i + 1}",
                port=8080 + i,
                protocol=_PROTOCOLS[i % 4],
                region="CN",
                ttl=120,
            ),
            now,
        )


def _make_app(pool=None, stats=None, **kwargs):
    return create_app(
        Level1Settings(),
        pool=pool if pool is not None else Level1Pool(max_size=500),
        stats=stats if stats is not None else ServiceStats(),
        start_tasks=False,
        **kwargs,
    )


async def test_status_fields_complete(running_app):
    pool = Level1Pool(max_size=500)
    await _seed_pool(pool, 5)
    stats = ServiceStats(total_pulled=100, total_entered=42)
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
        "pool_size",
        "counts",
        "api_call_count",
        "next_id",
        "errors",
        "drops",
    }
    assert data["total_pulled"] == 100
    assert data["total_entered"] == 42
    assert data["pool_size"] == 5
    assert data["counts"] == {"http": 2, "https": 1, "socks4": 1, "socks5": 1}
    assert data["next_id"] == 5
    assert data["uptime"] >= 0
    assert data["api_call_count"] >= 1
    assert data["errors"] == {
        "pull_failures": 0,
        "test_failures": 0,
        "ttl_sweep_failures": 0,
    }
    assert data["drops"] == 0


async def test_status_errors_fields(running_app):
    pool = Level1Pool(max_size=500)
    stats = ServiceStats(pull_failures=3, test_failures=2, ttl_sweep_failures=1, drops=4)
    app = _make_app(pool, stats)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/status")
    data = resp.json()["data"]
    assert data["errors"] == {
        "pull_failures": 3,
        "test_failures": 2,
        "ttl_sweep_failures": 1,
    }
    assert data["drops"] == 4


async def test_status_uptime_uses_start_time(running_app):
    app = _make_app(start_time=time.time() - 10)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/status")
    assert 9.0 <= resp.json()["data"]["uptime"] <= 11.0


async def test_count_endpoint(running_app):
    pool = Level1Pool(max_size=500)
    await _seed_pool(pool, 5)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/count")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["pool_size"] == 5
    assert data["counts"] == {"http": 2, "https": 1, "socks4": 1, "socks5": 1}


async def test_ips_endpoint_full_fields(running_app):
    pool = Level1Pool(max_size=500)
    await _seed_pool(pool, 3)
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
        "region",
        "ttl",
        "created_at",
    }
    assert data[0]["protocol"] == "http"
    assert data[0]["proxy_url"] == "http://1.2.3.1:8080"
    assert data[0]["region"] == "CN"
    assert data[0]["ttl"] == 120
    assert [d["id"] for d in data] == [0, 1, 2]


async def test_ips_after_middle_id(running_app):
    pool = Level1Pool(max_size=500)
    await _seed_pool(pool, 5)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/ips/after/2")
    assert resp.status_code == 200
    body = resp.json()
    assert [d["id"] for d in body["data"]] == [3, 4]
    assert body["max_id"] == 4


async def test_ips_after_out_of_range_empty(running_app):
    pool = Level1Pool(max_size=500)
    await _seed_pool(pool, 3)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/ips/after/999")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["max_id"] == 2


async def test_ips_after_empty_pool_max_id_null(running_app):
    app = _make_app()
    async with running_app(app) as client:
        resp = await client.get("/api/v1/ips/after/0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["max_id"] is None


async def test_empty_pool_not_500(running_app):
    app = _make_app()
    async with running_app(app) as client:
        r_ips = await client.get("/api/v1/ips")
        r_count = await client.get("/api/v1/count")
        r_status = await client.get("/api/v1/status")
    assert r_ips.status_code == 200
    assert r_ips.json()["data"] == []
    assert r_count.json()["data"]["pool_size"] == 0
    assert r_count.json()["data"]["counts"] == {
        "http": 0,
        "https": 0,
        "socks4": 0,
        "socks5": 0,
    }
    assert r_status.json()["data"]["pool_size"] == 0


async def test_api_call_count_increments(running_app):
    app = _make_app()
    async with running_app(app) as client:
        await client.get("/api/v1/count")
        await client.get("/api/v1/count")
        resp = await client.get("/api/v1/status")
    assert resp.json()["data"]["api_call_count"] == 3


async def test_non_v1_requests_not_counted(running_app):
    app = _make_app()
    async with running_app(app) as client:
        await client.get("/openapi.json")
        await client.get("/docs")
        resp = await client.get("/api/v1/status")
    assert resp.json()["data"]["api_call_count"] == 1


def test_create_app_defaults_when_config_missing(monkeypatch, tmp_path):
    import app.main as main

    monkeypatch.setattr(main, "_CONFIG_PATH", str(tmp_path / "missing.yaml"))
    app = create_app()
    assert app.state.settings.pool.max_size == 500
    assert app.state.pool.max_size == 500
    assert app.state.api_call_count == 0
