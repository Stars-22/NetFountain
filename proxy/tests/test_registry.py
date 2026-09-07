"""registry.py 测试：本地文件/远程 URL 加载、非法配置、重载原子替换、get/sites、reload_loop。

覆盖测试计划书 PX-REG-001 ~ 009。
"""
from __future__ import annotations

import asyncio

import aiohttp
import pytest
import yaml

from app.core.registry import Registry


def _write_routes(path, sites: list[dict]) -> None:
    path.write_text(yaml.safe_dump({"sites": sites}, sort_keys=False), encoding="utf-8")


class _StopLoop(Exception):
    pass


# PX-REG-001 本地文件加载
async def test_load_from_file(tmp_routes):
    reg = Registry(route_file=str(tmp_routes))
    n = await reg.load()
    assert n == 2
    assert reg.get("site_a").base_url == "http://127.0.0.1:8001"
    assert reg.get("site_a").target_url == "https://www.example.com"
    assert reg.get("site_b").base_url == "http://127.0.0.1:8002"


# PX-REG-002 远程 URL 加载（aioresponses 桩）
async def test_load_from_url(aio_mock):
    body = yaml.safe_dump({"sites": [{"name": "site_a", "base_url": "http://127.0.0.1:8001"}]})
    aio_mock.get("http://config.example/routes.yaml", body=body, status=200)
    reg = Registry(route_url="http://config.example/routes.yaml")
    n = await reg.load()
    assert n == 1
    assert reg.get("site_a").base_url == "http://127.0.0.1:8001"
    await reg.close()


async def test_load_from_url_http_error(aio_mock):
    aio_mock.get("http://config.example/routes.yaml", status=500, body="boom")
    reg = Registry(route_url="http://config.example/routes.yaml")
    with pytest.raises(ValueError, match="failed to fetch route url"):
        await reg.load()
    await reg.close()


async def test_load_from_url_connection_error(aio_mock):
    aio_mock.get(
        "http://config.example/routes.yaml",
        exception=aiohttp.ClientConnectionError("refused"),
    )
    reg = Registry(route_url="http://config.example/routes.yaml")
    with pytest.raises(ValueError, match="failed to fetch route url"):
        await reg.load()
    await reg.close()


async def test_load_from_url_empty_body(aio_mock):
    aio_mock.get("http://config.example/routes.yaml", body="", status=200)
    reg = Registry(route_url="http://config.example/routes.yaml")
    with pytest.raises(ValueError, match="missing 'sites'"):
        await reg.load()
    await reg.close()


async def test_load_from_url_non_mapping(aio_mock):
    aio_mock.get("http://config.example/routes.yaml", body="- 1\n- 2\n", status=200)
    reg = Registry(route_url="http://config.example/routes.yaml")
    with pytest.raises(ValueError, match="must be a mapping"):
        await reg.load()
    await reg.close()


def test_parse_rejects_non_mapping():
    with pytest.raises(ValueError, match="must be a mapping"):
        Registry._parse([1, 2])


async def test_load_no_source_raises():
    reg = Registry()
    with pytest.raises(ValueError, match="no route source"):
        await reg.load()


# PX-REG-003 非法配置（格式错/结构错）
async def test_load_malformed_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("sites: [\n", encoding="utf-8")
    reg = Registry(route_file=str(p))
    with pytest.raises(yaml.YAMLError):
        await reg.load()


async def test_load_missing_sites_key(tmp_path):
    p = tmp_path / "no_sites.yaml"
    p.write_text("foo: bar\n", encoding="utf-8")
    reg = Registry(route_file=str(p))
    with pytest.raises(ValueError, match="missing 'sites'"):
        await reg.load()


async def test_load_sites_not_list(tmp_path):
    p = tmp_path / "sites_not_list.yaml"
    p.write_text("sites: nope\n", encoding="utf-8")
    reg = Registry(route_file=str(p))
    with pytest.raises(ValueError, match="must be a list"):
        await reg.load()


async def test_load_site_entry_not_mapping(tmp_path):
    p = tmp_path / "bad_entry.yaml"
    p.write_text("sites:\n  - 42\n", encoding="utf-8")
    reg = Registry(route_file=str(p))
    with pytest.raises(ValueError, match="invalid site entry"):
        await reg.load()


async def test_load_missing_name(tmp_path):
    p = tmp_path / "no_name.yaml"
    p.write_text("sites:\n  - base_url: http://127.0.0.1:8001\n", encoding="utf-8")
    reg = Registry(route_file=str(p))
    with pytest.raises(ValueError, match="missing valid 'name'"):
        await reg.load()


# PX-REG-004 非法配置（缺 base_url）
async def test_load_missing_base_url(tmp_path):
    p = tmp_path / "no_base.yaml"
    p.write_text("sites:\n  - name: site_a\n", encoding="utf-8")
    reg = Registry(route_file=str(p))
    with pytest.raises(ValueError, match="missing valid 'base_url'"):
        await reg.load()


# PX-REG-005 重载原子替换
async def test_reload_atomic_replace(tmp_routes):
    reg = Registry(route_file=str(tmp_routes))
    await reg.load()
    _write_routes(tmp_routes, [
        {"name": "site_a", "base_url": "http://127.0.0.1:9001"},
        {"name": "site_b", "base_url": "http://127.0.0.1:9002"},
        {"name": "site_c", "base_url": "http://127.0.0.1:9003"},
    ])
    n = await reg.load()
    assert n == 3
    assert reg.get("site_a").base_url == "http://127.0.0.1:9001"
    assert reg.get("site_b").base_url == "http://127.0.0.1:9002"
    assert reg.get("site_c").base_url == "http://127.0.0.1:9003"


# PX-REG-006 重载失败保留旧表
async def test_reload_failure_keeps_old(tmp_routes):
    reg = Registry(route_file=str(tmp_routes))
    await reg.load()
    assert reg.get("site_a") is not None
    tmp_routes.write_text("sites: [\n", encoding="utf-8")  # 畸形 yaml
    with pytest.raises(yaml.YAMLError):
        await reg.load()
    assert reg.get("site_a").base_url == "http://127.0.0.1:8001"
    assert reg.get("site_b").base_url == "http://127.0.0.1:8002"


async def test_reload_file_deleted_keeps_old(tmp_routes):
    reg = Registry(route_file=str(tmp_routes))
    await reg.load()
    tmp_routes.unlink()
    with pytest.raises(FileNotFoundError):
        await reg.load()
    assert reg.get("site_a") is not None


# PX-REG-007 get 未配置站点返回 None
async def test_get_unconfigured_returns_none(registry):
    assert registry.get("not_configured") is None


# PX-REG-008 sites() 列表
async def test_sites_returns_all(registry):
    sites = registry.sites()
    assert len(sites) == 2
    names = {s.name for s in sites}
    assert names == {"site_a", "site_b"}
    assert all(isinstance(s.base_url, str) and s.base_url for s in sites)


# PX-REG-009 reload_loop 周期 ≈ reload_interval
async def test_reload_loop_period(monkeypatch, registry):
    sleeps: list[float] = []
    calls = 0

    async def fake_sleep(delay):
        nonlocal calls
        calls += 1
        sleeps.append(delay)
        if calls >= 2:
            raise _StopLoop

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    registry.reload_interval = 60.0
    with pytest.raises(_StopLoop):
        await registry.reload_loop()
    assert sleeps == [60.0, 60.0]


async def test_reload_loop_continues_after_failure(monkeypatch, registry):
    loads = 0

    async def fake_load():
        nonlocal loads
        loads += 1
        if loads == 1:
            raise ValueError("boom")

    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)
        if len(sleeps) >= 3:
            raise _StopLoop

    monkeypatch.setattr(registry, "load", fake_load)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    registry.reload_interval = 60.0
    with pytest.raises(_StopLoop):
        await registry.reload_loop()
    assert loads == 2  # 第 1 次失败被吞掉后继续，第 2 次成功
    assert sleeps == [60.0, 60.0, 60.0]