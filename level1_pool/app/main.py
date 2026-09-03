"""FastAPI 装配：lifespan 创建/关闭资源、启停后台任务，注册路由与调用计数中间件。

- 中间件只统计 /api/v1 业务端点调用次数（计数落在 ``app.state.api_call_count``）；
- ``create_app`` 全部组件可注入（供测试），未注入的在 lifespan 内按配置创建；
- 多供应商：``settings.providers``（global+providers 装配结果）中每个启用的供应商
  各创建独立 Provider + Tester + PullTask（独立 ``pull_lock`` 限频、独立待测队列，
  ``test_timeout``/``test_concurrency``/``test_buffer``/``test_workers`` 按条目独立
  生效），共享同一个 Level1Pool / ServiceStats / TtlSweeper / aiohttp 会话；
  注入 ``providers`` 列表时与启用的供应商配置按顺序一一对应；
- 旧格式（``settings.providers`` 为 None）退化为单供应商；
- 模块级 ``app`` 供 ``uvicorn app.main:app`` 使用（自动加载 config/level1_pool.yaml）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import aiohttp
from fastapi import FastAPI

from ip_pool_common.api import ApiCounterMiddleware, BizCodeLogMiddleware
from ip_pool_common.logging_setup import setup_logging

from .config import Level1Settings, ProviderRuntime, load_level1_settings
from .pool import Level1Pool, ProviderStats, ServiceStats
from .provider import BaseProvider, ProviderFactory
from .routes import router
from .tasks import PullTask, TtlSweeper
from .tester import Tester

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config", "level1_pool.yaml"
)


class V1CounterMiddleware(ApiCounterMiddleware):
    """只统计 /api/v1 业务端点的调用次数，计数写入 ``app.state.api_call_count``。"""

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/v1"):
            app = scope.get("app")
            state = getattr(app, "state", None)
            if state is not None:
                state.api_call_count = getattr(state, "api_call_count", 0) + 1
        await self.app(scope, receive, send)


def _runtime_from_legacy(settings: Level1Settings) -> ProviderRuntime:
    """旧格式单 provider 配置 + 顶层 test_* 字段 → 单供应商运行时配置。"""
    return ProviderRuntime(
        **settings.provider.model_dump(),
        test_timeout=settings.test_timeout,
        test_concurrency=settings.test_concurrency,
        test_buffer=settings.test_buffer,
        test_workers=settings.test_workers,
    )


def create_app(
    settings: Level1Settings | None = None,
    *,
    pool: Level1Pool | None = None,
    stats: ServiceStats | None = None,
    tester: Tester | None = None,
    provider: BaseProvider | None = None,
    providers: list[BaseProvider] | None = None,
    session: aiohttp.ClientSession | None = None,
    start_time: float | None = None,
    start_tasks: bool = True,
) -> FastAPI:
    """装配 FastAPI 应用；组件可注入，未注入的在 lifespan 内按配置创建。

    ``tester`` 注入时被全部供应商共用（测试桩语义）；``providers`` 注入列表须与
    启用的供应商配置数量一致（旧格式单供应商时也可用 ``provider`` 注入单个）。
    """
    if settings is None:
        if os.path.exists(_CONFIG_PATH):
            settings = load_level1_settings(_CONFIG_PATH)
        else:
            settings = load_level1_settings()

    setup_logging("level1_pool", level=settings.service.log_level)

    pool = pool or Level1Pool(max_size=settings.pool.max_size)
    stats = stats or ServiceStats()

    runtimes = list(settings.providers) if settings.providers else [_runtime_from_legacy(settings)]
    runtimes = [r for r in runtimes if r.enabled]
    if provider is not None and providers is None:
        providers = [provider]
    if providers is not None and len(providers) != len(runtimes):
        raise ValueError(
            f"injected providers ({len(providers)}) mismatch enabled provider configs ({len(runtimes)})"
        )
    provider_stats = {r.name: ProviderStats(name=r.name, type=r.type) for r in runtimes}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        own_session = session is None
        own_providers = providers is None
        active_session = (
            session if session is not None else (aiohttp.ClientSession() if start_tasks else None)
        )
        created_providers: list[BaseProvider] = []
        background: list[asyncio.Task] = []
        if start_tasks:
            for idx, rt in enumerate(runtimes):
                prov = (
                    providers[idx]
                    if providers is not None
                    else ProviderFactory.create(rt.type, rt, active_session)
                )
                created_providers.append(prov)
                active_tester = (
                    tester
                    if tester is not None
                    else Tester(timeout=rt.test_timeout, concurrency=rt.test_concurrency)
                )
                background.append(
                    asyncio.create_task(
                        PullTask(
                            prov,
                            active_tester,
                            pool,
                            stats,
                            rt.pull_count,
                            rt.pull_interval,
                            asyncio.Lock(),
                            buffer_size=rt.test_buffer,
                            test_workers=rt.test_workers,
                            name=rt.name,
                            provider_stats=provider_stats[rt.name],
                            default_ttl=rt.default_ttl,
                        ).run()
                    )
                )
            background.append(
                asyncio.create_task(
                    TtlSweeper(pool, settings.ttl_sweep_interval, stats).run()
                )
            )
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            if background:
                await asyncio.gather(*background, return_exceptions=True)
            if own_providers:
                for prov in created_providers:
                    await prov.close()
            if active_session is not None and own_session:
                await active_session.close()

    app = FastAPI(title="Level1 IP Pool", lifespan=lifespan)
    app.state.settings = settings
    app.state.pool = pool
    app.state.stats = stats
    app.state.provider_stats = provider_stats
    app.state.start_time = start_time if start_time is not None else time.time()
    app.state.api_call_count = 0
    app.add_middleware(V1CounterMiddleware)
    app.add_middleware(BizCodeLogMiddleware)
    app.include_router(router)
    return app


app = create_app()
