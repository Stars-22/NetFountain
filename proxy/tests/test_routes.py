"""routes.py 测试：/health、8 个透传端点、未配置站点、上游错误码。

覆盖测试计划书 PX-RT-001 ~ 009。
"""
from __future__ import annotations

import aiohttp

from app.config import ProxySettings
from app.main import create_app

STUB_OK = {"code": 0, "msg": "ok", "data": {"ok": True}}


def _proxy_app(registry=None):
    return create_app(ProxySettings(), registry=registry, start_reload=False)


def _json_calls(aio_mock, method: str, url_prefix: str):
    out = []
    for (m, u), calls in aio_mock.requests.items():
        if m.upper() == method.upper() and str(u).startswith(url_prefix):
            out.extend(calls)
    return out


async def _run(registry, client, method, path, **kwargs):
    resp = await client.request(method, path, **kwargs)
    return resp.status_code, resp.json()


# PX-RT-001 /health
async def test_health(running_app, registry):
    app = _proxy_app(registry)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["status"] == "ok"
    assert data["started_at"].endswith("Z")
    assert data["uptime"] >= 0
    stats = data["stats"]
    assert stats["total_calls"] >= 1
    assert "127.0.0.1" in stats["calls_by_ip"]
    sites = data["sites"]
    assert {s["name"] for s in sites} == {"site_a", "site_b"}
    by_name = {s["name"]: s for s in sites}
    assert by_name["site_a"]["base_url"] == "http://127.0.0.1:8001"
    assert by_name["site_a"]["target_url"] == "https://www.example.com"


async def test_health_empty_registry(running_app, tmp_path):
    import yaml

    from app.registry import Registry

    p = tmp_path / "empty.yaml"
    p.write_text(yaml.safe_dump({"sites": []}, sort_keys=False), encoding="utf-8")
    reg = Registry(route_file=str(p))
    await reg.load()
    app = _proxy_app(reg)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/health")
    assert resp.json()["data"]["sites"] == []


async def test_health_stats_after_passthrough(running_app, registry, aio_mock):
    aio_mock.get("http://127.0.0.1:8001/api/v1/status", payload=STUB_OK, status=200, repeat=True)
    aio_mock.get("http://127.0.0.1:8002/api/v1/status", payload=STUB_OK, status=200, repeat=True)
    app = _proxy_app(registry)
    async with running_app(app) as client:
        await client.get("/api/v1/site_a/status")
        await client.get("/api/v1/site_a/status")
        await client.get("/api/v1/site_b/status")
        resp = await client.get("/api/v1/health")
    stats = resp.json()["data"]["stats"]
    assert stats["calls_by_site"] == {"site_a": 2, "site_b": 1}
    assert stats["total_calls"] == 4  # 3 次透传 + 1 次 health


async def test_health_stats_errors(running_app, registry, aio_mock):
    aio_mock.get(
        "http://127.0.0.1:8001/api/v1/status",
        exception=aiohttp.ClientConnectionError("down"),
    )
    app = _proxy_app(registry)
    async with running_app(app) as client:
        await client.get("/api/v1/nope/status")
        await client.get("/api/v1/site_a/status")
        resp = await client.get("/api/v1/health")
    stats = resp.json()["data"]["stats"]
    assert stats["errors"] == {"40400": 1, "50200": 1}
    assert stats["calls_by_site"] == {}


# PX-RT-002 /{site}/status 透传
async def test_status_passthrough(running_app, registry, aio_mock):
    aio_mock.get("http://127.0.0.1:8001/api/v1/status", payload=STUB_OK, status=200)
    app = _proxy_app(registry)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/site_a/status")
    assert resp.status_code == 200
    assert resp.json() == STUB_OK


# PX-RT-003 /{site}/count 透传
async def test_count_passthrough(running_app, registry, aio_mock):
    payload = {"code": 0, "msg": "ok", "data": {"total": 5}}
    aio_mock.get("http://127.0.0.1:8001/api/v1/count", payload=payload, status=200)
    app = _proxy_app(registry)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/site_a/count")
    assert resp.status_code == 200
    assert resp.json() == payload


# PX-RT-004 /{site}/ips 透传
async def test_ips_passthrough(running_app, registry, aio_mock):
    payload = {"code": 0, "msg": "ok", "data": [{"id": 1}]}
    aio_mock.get("http://127.0.0.1:8001/api/v1/ips", payload=payload, status=200)
    app = _proxy_app(registry)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/site_a/ips")
    assert resp.status_code == 200
    assert resp.json() == payload


# PX-RT-005 /{site}/ips/acquire 透传
async def test_acquire_passthrough(running_app, registry, aio_mock):
    payload = {"code": 0, "msg": "ok", "data": {"id": 7, "ip": "10.0.0.7", "leased": True}}
    aio_mock.post("http://127.0.0.1:8001/api/v1/ips/acquire", payload=payload, status=200)
    app = _proxy_app(registry)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/site_a/ips/acquire")
    assert resp.status_code == 200
    assert resp.json() == payload


# 透传端点带 JSON body（原样透传到上游）
async def test_acquire_with_json_body_passthrough(running_app, registry, aio_mock):
    payload = {"code": 0, "msg": "ok", "data": {"id": 1}}
    aio_mock.post("http://127.0.0.1:8001/api/v1/ips/acquire", payload=payload, status=200)
    app = _proxy_app(registry)
    body = {"preferred": "http", "count": 3}
    async with running_app(app) as client:
        resp = await client.post("/api/v1/site_a/ips/acquire", json=body)
    assert resp.json() == payload
    calls = _json_calls(aio_mock, "POST", "http://127.0.0.1:8001/api/v1/ips/acquire")
    assert calls and calls[-1].kwargs["json"] == body


# 非 JSON body 不阻断透传
async def test_post_invalid_body_still_forwards(running_app, registry, aio_mock):
    payload = {"code": 0, "msg": "ok", "data": 0}
    aio_mock.post("http://127.0.0.1:8001/api/v1/ips/release-all", payload=payload, status=200)
    app = _proxy_app(registry)
    async with running_app(app) as client:
        resp = await client.post(
            "/api/v1/site_a/ips/release-all",
            content=b"not json",
            headers={"content-type": "text/plain"},
        )
    assert resp.json() == payload


# PX-RT-006 /{site}/ips/{id}/release 透传
async def test_release_passthrough(running_app, registry, aio_mock):
    payload = {"code": 0, "msg": "ok", "data": True}
    aio_mock.post("http://127.0.0.1:8001/api/v1/ips/42/release", payload=payload, status=200)
    app = _proxy_app(registry)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/site_a/ips/42/release")
    assert resp.status_code == 200
    assert resp.json() == payload


# PX-RT-007 /{site}/ips/{id} 删除透传
async def test_delete_passthrough(running_app, registry, aio_mock):
    payload = {"code": 0, "msg": "ok", "data": True}
    aio_mock.delete("http://127.0.0.1:8001/api/v1/ips/42", payload=payload, status=200)
    app = _proxy_app(registry)
    async with running_app(app) as client:
        resp = await client.delete("/api/v1/site_a/ips/42")
    assert resp.status_code == 200
    assert resp.json() == payload


# PX-RT-008 /{site}/ips/release-all 透传
async def test_release_all_passthrough(running_app, registry, aio_mock):
    payload = {"code": 0, "msg": "ok", "data": 3}
    aio_mock.post("http://127.0.0.1:8001/api/v1/ips/release-all", payload=payload, status=200)
    app = _proxy_app(registry)
    async with running_app(app) as client:
        resp = await client.post("/api/v1/site_a/ips/release-all")
    assert resp.status_code == 200
    assert resp.json() == payload


# PX-RT-009 未配置站点返回 40400
async def test_unknown_site_returns_40400(running_app, registry):
    app = _proxy_app(registry)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/not_configured/status")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 40400
    assert body["msg"] == "site not configured"
    assert body["data"] is None


async def test_unknown_site_all_endpoints_40400(running_app, registry):
    app = _proxy_app(registry)
    async with running_app(app) as client:
        checks = [
            (await client.get("/api/v1/nope/ips")).json(),
            (await client.post("/api/v1/nope/ips/acquire")).json(),
            (await client.post("/api/v1/nope/ips/42/release")).json(),
            (await client.delete("/api/v1/nope/ips/42")).json(),
            (await client.post("/api/v1/nope/ips/release-all")).json(),
        ]
    for body in checks:
        assert body["code"] == 40400


# 上游不可达/超时 → 50200
async def test_upstream_error_returns_50200(running_app, registry, aio_mock):
    aio_mock.get(
        "http://127.0.0.1:8001/api/v1/status",
        exception=aiohttp.ClientConnectionError("down"),
    )
    app = _proxy_app(registry)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/site_a/status")
    assert resp.status_code == 502
    body = resp.json()
    assert body["code"] == 50200
    assert body["msg"] == "upstream error"
    assert body["data"] is None


async def test_upstream_timeout_returns_50200(running_app, registry, aio_mock):
    aio_mock.get("http://127.0.0.1:8001/api/v1/status", timeout=True)
    app = _proxy_app(registry)
    async with running_app(app) as client:
        resp = await client.get("/api/v1/site_a/status")
    assert resp.status_code == 502
    assert resp.json()["code"] == 50200


# 单站点故障隔离（好站仍正常）
async def test_fault_isolation_at_routes(running_app, registry, aio_mock):
    aio_mock.get(
        "http://127.0.0.1:8001/api/v1/status",
        exception=ConnectionError("site_a down"),
    )
    aio_mock.get(
        "http://127.0.0.1:8002/api/v1/status", payload=STUB_OK, status=200
    )
    app = _proxy_app(registry)
    async with running_app(app) as client:
        r_bad = await client.get("/api/v1/site_a/status")
        r_good = await client.get("/api/v1/site_b/status")
    assert r_bad.json()["code"] == 50200
    assert r_good.json() == STUB_OK