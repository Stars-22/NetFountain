"""多供应商配置与装配测试：global 深合并 / 名称与必填校验 / 旧格式兼容 /
enabled 开关 / 多拉取器共享池 / PullTask 供应商明细统计 / 注入数量校验。
"""
from __future__ import annotations

import asyncio

import pytest

import mock_provider
from app import tester as tester_mod
from app.config import (
    Level1Settings,
    PoolConfig,
    ProviderRuntime,
    load_level1_pool_config,
)
from app.main import create_app
from app.pool import Level1Pool, ProviderStats, ServiceStats
from app.tasks import PullTask
from ip_pool_common.models import Protocol, ProviderIp


async def _always_pass(ip):
    return True, 1.0


def _write(tmp_config, text: str) -> str:
    path = tmp_config / "level1_pool.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# 配置装配：global 深合并 / 校验 / 旧格式兼容
# ---------------------------------------------------------------------------

GLOBAL_AND_PROVIDERS = """
service:
  port: 8200
pool:
  max_size: 100
ttl_sweep_interval: 6.0
global:
  type: default_http
  pull_count: 4
  pull_interval: 0.05
  pull_timeout: 2.0
  test_timeout: 1.0
  test_concurrency: 8
  test_buffer: 10
providers:
  - name: a
    api_url: http://x.example/a
    test_workers: 2
  - name: b
    api_url: http://x.example/b
    test_concurrency: 16
    test_buffer: 5
    test_workers: null
"""


def test_global_deep_merge_and_fallback(tmp_config):
    cfg = load_level1_pool_config(_write(tmp_config, GLOBAL_AND_PROVIDERS))
    a, b = cfg.providers
    assert a.name == "a" and a.type == "default_http"
    assert a.pull_count == 4 and a.pull_interval == 0.05 and a.pull_timeout == 2.0
    assert a.test_timeout == 1.0 and a.test_concurrency == 8 and a.test_buffer == 10
    assert a.test_workers == 2 and a.enabled is True
    assert b.pull_count == 4 and b.pull_interval == 0.05  # 回退 global
    assert b.test_concurrency == 16 and b.test_buffer == 5  # 条目覆盖 global
    assert b.test_workers is None and b.test_timeout == 1.0
    assert cfg.service.port == 8200
    assert cfg.pool.max_size == 100
    assert cfg.ttl_sweep_interval == 6.0


def test_auto_name_when_missing(tmp_config):
    cfg = load_level1_pool_config(_write(tmp_config, """
global:
  type: default_http
providers:
  - api_url: http://x.example/a
  - api_url: http://x.example/b
"""))
    assert [p.name for p in cfg.providers] == ["provider_1", "provider_2"]


def test_missing_type_raises(tmp_config):
    path = _write(tmp_config, """
providers:
  - name: a
    api_url: http://x.example/a
""")
    with pytest.raises(ValueError, match="missing required 'type'"):
        load_level1_pool_config(path)


def test_duplicate_name_raises(tmp_config):
    path = _write(tmp_config, """
providers:
  - name: a
    type: default_http
    api_url: http://x.example/a
  - name: a
    type: default_http
    api_url: http://x.example/b
""")
    with pytest.raises(ValueError, match="duplicate provider name"):
        load_level1_pool_config(path)


def test_empty_providers_raises(tmp_config):
    path = _write(tmp_config, "providers: []\n")
    with pytest.raises(ValueError, match="non-empty"):
        load_level1_pool_config(path)


def test_legacy_format_compat(tmp_config):
    path = _write(tmp_config, """
service:
  port: 8100
provider:
  type: default_http
  api_url: http://p.example/api
  pull_count: 7
pool:
  max_size: 100
test_timeout: 2.5
test_concurrency: 20
test_buffer: 30
test_workers: 4
""")
    cfg = load_level1_pool_config(path)
    assert len(cfg.providers) == 1
    rt = cfg.providers[0]
    assert rt.name == "provider_1" and rt.type == "default_http"
    assert rt.pull_count == 7
    assert (rt.test_timeout, rt.test_concurrency, rt.test_buffer, rt.test_workers) == (2.5, 20, 30, 4)
    assert cfg.service.port == 8100 and cfg.pool.max_size == 100


# ---------------------------------------------------------------------------
# 运行时装配：多拉取器共享池 / enabled 开关 / status 明细 / 注入校验
# ---------------------------------------------------------------------------


def _api_url(base_url: str) -> str:
    return f"{base_url}/proxies"


def _runtime(
    name: str, api_url: str, *, pull_count: int = 3, enabled: bool = True
) -> ProviderRuntime:
    return ProviderRuntime(
        name=name,
        type="default_http",
        api_url=api_url,
        pull_count=pull_count,
        pull_interval=0.05,
        pull_timeout=2.0,
        test_timeout=1.0,
        test_concurrency=5,
        test_buffer=10,
        test_workers=1,
        enabled=enabled,
    )


def _multi_app(api_url: str, *, enabled_b: bool = True):
    settings = Level1Settings(
        providers=[
            _runtime("a", api_url, pull_count=3),
            _runtime("b", api_url, pull_count=2, enabled=enabled_b),
        ],
        pool=PoolConfig(max_size=50),
        ttl_sweep_interval=5.0,
    )
    tester = tester_mod.Tester(timeout=1.0, concurrency=5, test_fn=_always_pass)
    return create_app(settings, tester=tester, start_tasks=True)


async def test_multi_providers_shared_pool(mock_server, running_app):
    mock_provider.state.count = 3
    app = _multi_app(_api_url(mock_server))
    async with running_app(app) as client:
        status = None
        for _ in range(150):
            status = (await client.get("/api/v1/status")).json()["data"]
            provs = status["providers"]
            if len(provs) == 2 and all(
                p["total_pulled"] > 0 and p["total_entered"] > 0 for p in provs
            ):
                break
            await asyncio.sleep(0.02)
        assert len(provs) == 2
        assert {p["name"] for p in provs} == {"a", "b"}
        assert all(p["type"] == "default_http" for p in provs)
        # 全局汇总 = 各供应商明细之和
        assert status["total_pulled"] == sum(p["total_pulled"] for p in provs)
        assert status["total_entered"] == sum(p["total_entered"] for p in provs)
        assert status["pool_size"] > 0


async def test_disabled_provider_not_started(mock_server, running_app):
    mock_provider.state.count = 2
    app = _multi_app(_api_url(mock_server), enabled_b=False)
    async with running_app(app) as client:
        await asyncio.sleep(0.3)
        status = (await client.get("/api/v1/status")).json()["data"]
        provs = status["providers"]
        assert [p["name"] for p in provs] == ["a"]
        assert provs[0]["total_pulled"] > 0
        assert status["total_pulled"] == provs[0]["total_pulled"]


class _DummyProvider:
    async def pull(self, count):
        return []

    async def close(self):
        pass


def test_injected_providers_count_mismatch():
    settings = Level1Settings(
        providers=[_runtime("a", "http://x/a"), _runtime("b", "http://x/b")]
    )
    with pytest.raises(ValueError, match="mismatch"):
        create_app(settings, providers=[_DummyProvider()], start_tasks=False)


# ---------------------------------------------------------------------------
# PullTask 供应商明细统计
# ---------------------------------------------------------------------------


class _PassTester:
    async def test_many(self, ips):
        return list(ips)


class _GoodProvider:
    def __init__(self):
        self.n = 0

    async def pull(self, count):
        out = []
        for _ in range(count):
            self.n += 1
            out.append(
                ProviderIp(
                    ip=f"10.9.0.{self.n}", port=8000 + self.n, protocol=Protocol.HTTP
                )
            )
        return out


class _FailingProvider:
    async def pull(self, count):
        raise RuntimeError("boom")


async def _run_ticks(task: PullTask, settle: float = 0.08) -> None:
    t = asyncio.create_task(task.run())
    await asyncio.sleep(settle)
    await task.join()
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)


async def test_pulltask_provider_stats_success():
    pool = Level1Pool(max_size=100)
    stats = ServiceStats()
    pstats = ProviderStats(name="p", type="default_http")
    task = PullTask(
        _GoodProvider(),
        _PassTester(),
        pool,
        stats,
        3,
        0.01,
        asyncio.Lock(),
        buffer_size=2,
        test_workers=1,
        name="p",
        provider_stats=pstats,
    )
    await _run_ticks(task)
    assert pstats.total_pulled == stats.total_pulled > 0
    assert pstats.total_entered == stats.total_entered == pstats.total_pulled
    assert pool.size() == pstats.total_entered
    assert pstats.pull_failures == 0 and pstats.drops == 0


async def test_pulltask_provider_stats_failures():
    pool = Level1Pool(max_size=10)
    stats = ServiceStats()
    pstats = ProviderStats(name="p", type="default_http")
    task = PullTask(
        _FailingProvider(),
        _PassTester(),
        pool,
        stats,
        3,
        0.01,
        asyncio.Lock(),
        buffer_size=2,
        test_workers=1,
        name="p",
        provider_stats=pstats,
    )
    await _run_ticks(task)
    assert pstats.pull_failures == stats.pull_failures > 0
    assert pstats.total_pulled == 0 and pstats.total_entered == 0
