"""集成测试：mock 供应商 → 拉取 → 测试 → 入池 → API 查询闭环。

覆盖 L1-INT-001/002/003：闭环、容量淘汰、供应商故障恢复。
"""
from __future__ import annotations

import asyncio

import mock_provider
import pytest

from app.config import Level1Settings, PoolConfig, ProviderConfig
from app.main import create_app
from app import tester as tester_mod


async def _always_pass(ip):
    return True, 1.0


def _integration_app(
    provider_api_url: str,
    *,
    max_size: int = 50,
    pull_count: int = 3,
    pull_interval: float = 0.05,
    tester=None,
):
    settings = Level1Settings(
        provider=ProviderConfig(
            type="default_http",
            api_url=provider_api_url,
            api_key="",
            pull_count=pull_count,
            pull_interval=pull_interval,
            pull_timeout=2.0,
        ),
        pool=PoolConfig(max_size=max_size),
        test_timeout=1.0,
        test_concurrency=5,
        ttl_sweep_interval=5.0,
    )
    tester = tester or tester_mod.Tester(
        timeout=1.0, concurrency=5, test_fn=_always_pass
    )
    return create_app(settings, tester=tester, start_tasks=True)


def _api_url(base_url: str) -> str:
    return f"{base_url}/proxies"


async def test_integration_closed_loop(mock_server, running_app):
    mock_provider.state.count = 3
    app = _integration_app(_api_url(mock_server), max_size=50, pull_count=3, pull_interval=0.05)
    async with running_app(app) as client:
        await asyncio.sleep(0.5)
        # 等待测试管线排空：total_entered 追上 total_pulled（pass_all → 全通过）
        status = None
        for _ in range(100):
            status = (await client.get("/api/v1/status")).json()["data"]
            if status["total_pulled"] > 0 and status["total_entered"] >= status["total_pulled"]:
                break
            await asyncio.sleep(0.02)
        assert status["total_pulled"] > 0
        assert status["total_entered"] > 0
        assert status["pool_size"] > 0
        assert status["pool_size"] == status["total_entered"]  # pass_all → 全通过
        ips = (await client.get("/api/v1/ips")).json()["data"]
        assert len(ips) == status["pool_size"]
        assert all(rec["ip"].startswith("127.0.0.") for rec in ips)
        assert all(rec["protocol"] == "http" for rec in ips)
        count = (await client.get("/api/v1/count")).json()["data"]
        assert count["pool_size"] == status["pool_size"]


async def test_integration_capacity_eviction(mock_server, running_app):
    mock_provider.state.count = 2
    app = _integration_app(
        _api_url(mock_server), max_size=3, pull_count=2, pull_interval=0.02
    )
    async with running_app(app) as client:
        await asyncio.sleep(0.3)
        status = (await client.get("/api/v1/status")).json()["data"]
        assert 0 < status["pool_size"] <= 3
        ips = (await client.get("/api/v1/ips")).json()["data"]
        assert len(ips) <= 3
        ids = [rec["id"] for rec in ips]
        assert ids == list(
            range(status["next_id"] - len(ids), status["next_id"])
        )


async def test_integration_supplier_down_then_recovers(mock_server, running_app):
    mock_provider.state.count = 3
    mock_provider.state.failure_rate = 1.0
    app = _integration_app(_api_url(mock_server), max_size=50, pull_count=3, pull_interval=0.03)
    async with running_app(app) as client:
        await asyncio.sleep(0.25)
        status = (await client.get("/api/v1/status")).json()["data"]
        assert status["total_pulled"] == 0
        assert status["pool_size"] == 0

        mock_provider.state.failure_rate = 0.0
        await asyncio.sleep(0.3)
        status = None
        for _ in range(100):
            status = (await client.get("/api/v1/status")).json()["data"]
            if status["total_pulled"] > 0 and status["pool_size"] > 0:
                break
            await asyncio.sleep(0.02)
        assert status["total_pulled"] > 0
        assert status["pool_size"] > 0