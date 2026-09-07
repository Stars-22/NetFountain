"""tester.py 测试：过滤 / 并发受控 / 仅测代理可达（不测出口）。"""
from __future__ import annotations

import asyncio

import pytest

import app.testing.tester as tester_mod


async def test_test_many_returns_only_passing(make_ip):
    async def _fn(ip):
        return (ip.port % 2 == 0, float(ip.port))

    tester = tester_mod.Tester(timeout=1.0, concurrency=4, test_fn=_fn)
    ips = [make_ip(1), make_ip(2), make_ip(3), make_ip(4)]
    passed = await tester.test_many(ips)
    assert [ip.port for ip in passed] == [8002, 8004]


async def test_test_many_uses_proxy_reachability_only(monkeypatch, make_ip):
    """只调用 proxy_reachability_test，绝不调用 site_test（不测出口）。"""
    called = []

    async def _fake(proxy_url, timeout=3.0):
        called.append(proxy_url)
        return True, 1.0

    monkeypatch.setattr("app.testing.tester.proxy_reachability_test", _fake)
    assert not hasattr(tester_mod, "site_test")
    tester = tester_mod.Tester(timeout=1.0, concurrency=4)
    ips = [make_ip(1), make_ip(2)]
    passed = await tester.test_many(ips)
    assert len(passed) == 2
    urls = {f"http://10.0.0.{i}:{8000 + i}" for i in (1, 2)}
    assert set(called) == urls


async def test_proxy_reachable_but_egress_broken_still_passes(make_ip):
    """代理可达（握手成功）即便出口失败也判通过——只验代理连接，不验出口。"""

    async def _fn(ip):
        return True, 5.0

    tester = tester_mod.Tester(timeout=1.0, concurrency=4, test_fn=_fn)
    passed = await tester.test_many([make_ip(1), make_ip(2)])
    assert len(passed) == 2


async def test_concurrency_capped(make_ip):
    active = 0
    max_active = 0

    async def _fn(ip):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return True, 1.0

    tester = tester_mod.Tester(timeout=1.0, concurrency=2, test_fn=_fn)
    await tester.test_many([make_ip(i) for i in range(10)])
    assert max_active <= 2


async def test_test_many_empty_input():
    tester = tester_mod.Tester()
    assert await tester.test_many([]) == []


async def test_test_many_exception_in_test_fn_excluded(make_ip):
    async def _fn(ip):
        if ip.port == 8002:
            raise RuntimeError("boom")
        return True, 1.0

    tester = tester_mod.Tester(timeout=1.0, concurrency=4, test_fn=_fn)
    passed = await tester.test_many([make_ip(1), make_ip(2), make_ip(3)])
    assert [ip.port for ip in passed] == [8001, 8003]


async def test_timeout_propagated_to_reachability_test(monkeypatch, make_ip):
    captured = {}

    async def _fake(proxy_url, timeout=3.0):
        captured["timeout"] = timeout
        return True, 1.0

    monkeypatch.setattr("app.testing.tester.proxy_reachability_test", _fake)
    tester = tester_mod.Tester(timeout=2.5, concurrency=2)
    await tester.test_many([make_ip(1)])
    assert captured["timeout"] == 2.5
