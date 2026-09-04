"""routes.py + main.py 测试：7+1 端点契约 / 空池错误码 / 不存在 id / 计数递增 /
acquire 提取策略与筛选参数 / acquire-batch 批量提取。

覆盖测试计划书 L2-RT-001 ~ 010 及提取策略扩展用例。
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


# ---------------------------------------------------------------------------
# acquire 提取策略 / 筛选参数
# ---------------------------------------------------------------------------


async def _seed_with_latencies(pool, make_l2, make_ip, latencies, ttl=3600.0):
    now = time.time()
    for i, lat in enumerate(latencies, start=1):
        await pool.upsert(
            make_l2(make_ip(i, ttl=ttl), latency=float(lat), created_at=now)
        )


async def test_acquire_strategy_latency_asc_via_api(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_with_latencies(pool, make_l2, make_ip, [300, 100, 200])
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire?strategy=latency_asc")
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["ip"] == "10.0.0.2"
    assert resp.json()["data"]["leased"] is True


async def test_acquire_strategy_remaining_desc_via_api(running_app, make_l2, make_ip):
    pool = Level2Pool()
    now = time.time()
    await pool.upsert(make_l2(make_ip(1, ttl=100.0), created_at=now))
    await pool.upsert(make_l2(make_ip(2, ttl=None), created_at=now))
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire?strategy=remaining_desc")
    assert resp.json()["data"]["ip"] == "10.0.0.2"  # ttl=None → 剩余时间无穷大


async def test_acquire_default_params_unchanged(running_app, make_l2, make_ip):
    """不带参数 = 旧行为：最新优先、不筛选。"""
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=3, leased_every=99)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire")
    assert resp.json()["data"]["id"] == 2  # 最新入池的 10.0.0.3


async def test_acquire_filter_max_latency_ms_via_api(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=3, leased_every=99)  # 延迟 100/200/300
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire?max_latency_ms=250")
    assert resp.json()["data"]["ip"] == "10.0.0.2"  # 最新且延迟达标者


async def test_acquire_filter_excludes_all_returns_emptypool(
    running_app, make_l2, make_ip
):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=2, leased_every=99)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire?max_latency_ms=50")
    assert resp.json()["code"] == 40402
    assert app.state.stats.empty_acquires == 1
    assert pool.stats().leased_total == 0


async def test_acquire_filter_min_remaining_sec_via_api(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_with_latencies(pool, make_l2, make_ip, [100, 200], ttl=3600.0)
    app = _make_app(pool)
    async with running_app(app) as client:
        ok_resp = await client.post("/api/v1/ips/acquire?min_remaining_sec=60")
        assert ok_resp.json()["code"] == 0
        empty_resp = await client.post("/api/v1/ips/acquire?min_remaining_sec=7200")
    assert empty_resp.json()["code"] == 40402


async def test_acquire_invalid_strategy_returns_param_error(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=1, leased_every=99)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire?strategy=fastest")
    body = resp.json()
    assert body["code"] == 40000
    assert "strategy" in body["msg"]
    assert body["data"] is None
    assert pool.stats().leased_total == 0


async def test_acquire_invalid_numeric_filters_return_param_error(running_app):
    app = _make_app()
    async with running_app(app) as client:
        r1 = await client.post("/api/v1/ips/acquire?max_latency_ms=abc")
        r2 = await client.post("/api/v1/ips/acquire?min_remaining_sec=-5")
        r3 = await client.post("/api/v1/ips/acquire?max_latency_ms=nan")
    assert all(r.json()["code"] == 40000 for r in (r1, r2, r3))


# ---------------------------------------------------------------------------
# acquire-batch 批量提取
# ---------------------------------------------------------------------------


async def test_acquire_batch_latest_order_via_api(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=3, leased_every=99)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire-batch?count=2")
    body = resp.json()
    assert body["code"] == 0
    assert [rec["ip"] for rec in body["data"]] == ["10.0.0.3", "10.0.0.2"]
    assert all(rec["leased"] for rec in body["data"])
    assert pool.stats().leased_total == 2


async def test_acquire_batch_latency_asc_via_api(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_with_latencies(pool, make_l2, make_ip, [300, 100, 200])
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post(
            "/api/v1/ips/acquire-batch?count=2&strategy=latency_asc"
        )
    assert [rec["ip"] for rec in resp.json()["data"]] == ["10.0.0.2", "10.0.0.3"]
    latencies = [rec["latency_ms"] for rec in resp.json()["data"]]
    assert latencies == sorted(latencies)


async def test_acquire_batch_partial_returns_available(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=2, leased_every=99)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire-batch?count=10")
    body = resp.json()
    assert body["code"] == 0
    assert len(body["data"]) == 2


async def test_acquire_batch_empty_returns_emptypool(running_app):
    app = _make_app()
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire-batch?count=3")
    body = resp.json()
    assert body["code"] == 40402
    assert body["data"] is None
    assert app.state.stats.empty_acquires == 1


async def test_acquire_batch_all_leased_returns_emptypool(
    running_app, make_l2, make_ip
):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=2, leased_every=1)
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire-batch?count=2")
    assert resp.json()["code"] == 40402


async def test_acquire_batch_missing_count_returns_param_error(running_app):
    app = _make_app()
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire-batch")
    body = resp.json()
    assert body["code"] == 40000
    assert "count" in body["msg"]


async def test_acquire_batch_invalid_count_returns_param_error(running_app):
    app = _make_app()
    async with running_app(app) as client:
        r0 = await client.post("/api/v1/ips/acquire-batch?count=0")
        r_neg = await client.post("/api/v1/ips/acquire-batch?count=-1")
        r_float = await client.post("/api/v1/ips/acquire-batch?count=1.5")
        r_text = await client.post("/api/v1/ips/acquire-batch?count=abc")
    assert all(r.json()["code"] == 40000 for r in (r0, r_neg, r_float, r_text))


async def test_acquire_batch_invalid_strategy_returns_param_error(running_app):
    app = _make_app()
    async with running_app(app) as client:
        resp = await client.post("/api/v1/ips/acquire-batch?count=1&strategy=xxx")
    assert resp.json()["code"] == 40000


async def test_acquire_batch_with_filters_via_api(running_app, make_l2, make_ip):
    pool = Level2Pool()
    await _seed_pool(pool, make_l2, make_ip, n=4, leased_every=99)  # 延迟 100..400
    app = _make_app(pool)
    async with running_app(app) as client:
        resp = await client.post(
            "/api/v1/ips/acquire-batch?count=10&max_latency_ms=250"
        )
    body = resp.json()
    assert body["code"] == 0
    assert [rec["ip"] for rec in body["data"]] == ["10.0.0.2", "10.0.0.1"]
    assert pool.stats().leased_total == 2