"""集成测试：mock 二级池 → 用户经代理访问闭环、热更新、多站点并发、故障隔离、重载失败容错。

覆盖测试计划书 PX-INT-001 ~ 003、PX-ROB-001 ~ 002。
"""
from __future__ import annotations

import asyncio

import yaml

from app.config import ProxySettings
from app.core.registry import Registry
from app.main import create_app


def _write_routes(path, sites: list[dict]) -> None:
    path.write_text(yaml.safe_dump({"sites": sites}, sort_keys=False), encoding="utf-8")


def _proxy_app(registry, start_reload=False):
    return create_app(ProxySettings(), registry=registry, start_reload=start_reload)


# PX-INT-001 mock 二级池 → 用户经代理 acquire 成功
async def test_integration_acquire_roundtrip(mock_level2, running_app, tmp_path):
    async with mock_level2("site_a") as servers:
        site, state, base_url = servers[0]
        p = tmp_path / "routes.yaml"
        _write_routes(p, [{"name": site, "base_url": base_url}])
        reg = Registry(route_file=str(p))
        await reg.load()
        app = _proxy_app(reg)
        async with running_app(app) as client:
            resp = await client.post(f"/api/v1/{site}/ips/acquire")
            assert resp.status_code == 200
            body = resp.json()
            assert body["code"] == 0
            assert body["data"]["site"] == site
            assert body["data"]["leased"] is True
            cnt = await client.get(f"/api/v1/{site}/count")
            assert cnt.json()["data"]["total"] == 1
            assert len(state.pool) == 1


# PX-INT-001 完整契约：status/count/acquire/release/delete/release-all 均经代理直通
async def test_integration_full_contract(mock_level2, running_app, tmp_path):
    async with mock_level2("site_a") as servers:
        site, _, base_url = servers[0]
        p = tmp_path / "routes.yaml"
        _write_routes(p, [{"name": site, "base_url": base_url}])
        reg = Registry(route_file=str(p))
        await reg.load()
        app = _proxy_app(reg)
        async with running_app(app) as client:
            r = await client.get(f"/api/v1/{site}/status")
            assert r.json()["data"]["site"] == site

            r = await client.post(f"/api/v1/{site}/ips/acquire")
            rid = r.json()["data"]["id"]
            r = await client.post(f"/api/v1/{site}/ips/{rid}/release")
            assert r.json()["data"] is True

            r = await client.post(f"/api/v1/{site}/ips/acquire")
            rid = r.json()["data"]["id"]
            r = await client.delete(f"/api/v1/{site}/ips/{rid}")
            assert r.json()["data"] is True

            r = await client.post(f"/api/v1/{site}/ips/release-all")
            assert r.json()["code"] == 0


# PX-INT-002 改配置后热更新生效（新站点无需重启即可访问）
async def test_hot_reload_adds_site(mock_level2, running_app, tmp_path):
    async with mock_level2("site_a", "site_b") as servers:
        (name_a, _, url_a), (name_b, state_b, url_b) = servers
        state_b.add(2)
        p = tmp_path / "routes.yaml"
        _write_routes(p, [{"name": name_a, "base_url": url_a}])
        reg = Registry(route_file=str(p), reload_interval=0.05)
        await reg.load()
        app = _proxy_app(reg, start_reload=True)
        async with running_app(app) as client:
            r = await client.get(f"/api/v1/{name_b}/ips")
            assert r.json()["code"] == 40400

            _write_routes(p, [
                {"name": name_a, "base_url": url_a},
                {"name": name_b, "base_url": url_b},
            ])
            await asyncio.sleep(0.3)  # 等待 reload_loop 触发重载

            r = await client.get(f"/api/v1/{name_b}/ips")
            assert r.json()["code"] == 0
            assert r.json()["data"][0]["site"] == name_b


# PX-INT-003 多站点并发访问互不串扰
async def test_multi_site_concurrent_no_crosstalk(mock_level2, running_app, tmp_path):
    async with mock_level2("site_a", "site_b") as servers:
        (name_a, state_a, url_a), (name_b, state_b, url_b) = servers
        p = tmp_path / "routes.yaml"
        _write_routes(p, [
            {"name": name_a, "base_url": url_a},
            {"name": name_b, "base_url": url_b},
        ])
        reg = Registry(route_file=str(p))
        await reg.load()
        app = _proxy_app(reg)
        async with running_app(app) as client:
            async def acquire(name: str):
                r = await client.post(f"/api/v1/{name}/ips/acquire")
                return r.json()["data"]["site"]

            results = await asyncio.gather(
                *[acquire(name_a if i % 2 == 0 else name_b) for i in range(20)]
            )
        assert results == [name_a if i % 2 == 0 else name_b for i in range(20)]
        assert len(state_a.pool) == 10
        assert len(state_b.pool) == 10


# PX-ROB-001 重载失败服务不中断
async def test_reload_failure_service_continues(mock_level2, running_app, tmp_path):
    async with mock_level2("site_a") as servers:
        site, _, base_url = servers[0]
        p = tmp_path / "routes.yaml"
        _write_routes(p, [{"name": site, "base_url": base_url}])
        reg = Registry(route_file=str(p), reload_interval=0.05)
        await reg.load()
        app = _proxy_app(reg, start_reload=True)
        async with running_app(app) as client:
            r = await client.get(f"/api/v1/{site}/status")
            assert r.json()["code"] == 0

            p.write_text("sites: [\n", encoding="utf-8")  # 破坏配置
            await asyncio.sleep(0.3)  # reload_loop 连续失败

            r = await client.get(f"/api/v1/{site}/status")
            assert r.json()["code"] == 0  # 旧路由表继续生效


# PX-ROB-002 单站点故障隔离（仅该站点报错）
async def test_single_site_failure_isolated(mock_level2, running_app, tmp_path):
    async with mock_level2("site_b") as servers:
        name_b, _, url_b = servers[0]
        p = tmp_path / "routes.yaml"
        _write_routes(p, [
            {"name": "site_a", "base_url": "http://127.0.0.1:1"},  # 不可达端口
            {"name": name_b, "base_url": url_b},
        ])
        reg = Registry(route_file=str(p))
        await reg.load()
        app = _proxy_app(reg)
        async with running_app(app) as client:
            r_bad = await client.get("/api/v1/site_a/status")
            r_good = await client.get(f"/api/v1/{name_b}/status")
        assert r_bad.json()["code"] == 50200
        assert r_good.json()["code"] == 0
        assert r_good.json()["data"]["site"] == name_b