"""FastAPI 装配：lifespan 创建/关闭资源、启停后台任务，注册路由与调用计数中间件。

- 中间件只统计 /api/v1 业务端点调用次数（计数落在 ``app.state.api_call_count``）；
- ``create_app`` 全部组件可注入（供测试），未注入的在 lifespan 内按配置创建；
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

from ip_pool_common.api import ApiCounterMiddleware

from .config import Level1Settings, load_level1_settings
from .pool import Level1Pool, ServiceStats
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


def create_app(
    settings: Level1Settings | None = None,
    *,
    pool: Level1Pool | None = None,
    stats: ServiceStats | None = None,
    tester: Tester | None = None,
    provider: BaseProvider | None = None,
    session: aiohttp.ClientSession | None = None,
    start_time: float | None = None,
    start_tasks: bool = True,
) -> FastAPI:
    """装配 FastAPI 应用；组件可注入，未注入的在 lifespan 内按配置创建。"""
    if settings is None:
        if os.path.exists(_CONFIG_PATH):
            settings = load_level1_settings(_CONFIG_PATH)
        else:
            settings = load_level1_settings()

    pool = pool or Level1Pool(max_size=settings.pool.max_size)
    stats = stats or ServiceStats()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        own_session = session is None
        own_provider = provider is None
        active_session = (
            session if session is not None else (aiohttp.ClientSession() if start_tasks else None)
        )
        active_provider = provider
        if active_provider is None and start_tasks:
            active_provider = ProviderFactory.create(
                settings.provider.type, settings.provider, active_session
            )
        active_tester = tester if tester is not None else (
            Tester(timeout=settings.test_timeout, concurrency=settings.test_concurrency)
            if start_tasks
            else None
        )

        background: list[asyncio.Task] = []
        if start_tasks:
            pull_lock = asyncio.Lock()
            background = [
                asyncio.create_task(
                    PullTask(
                        active_provider,
                        active_tester,
                        pool,
                        stats,
                        settings.provider.pull_count,
                        settings.provider.pull_interval,
                        pull_lock,
                        buffer_size=settings.test_buffer,
                        test_workers=settings.test_workers,
                    ).run()
                ),
                asyncio.create_task(
                    TtlSweeper(pool, settings.ttl_sweep_interval).run()
                ),
            ]
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            if background:
                await asyncio.gather(*background, return_exceptions=True)
            if active_provider is not None and own_provider:
                await active_provider.close()
            if active_session is not None and own_session:
                await active_session.close()

    app = FastAPI(title="Level1 IP Pool", lifespan=lifespan)
    app.state.settings = settings
    app.state.pool = pool
    app.state.stats = stats
    app.state.start_time = start_time if start_time is not None else time.time()
    app.state.api_call_count = 0
    app.add_middleware(V1CounterMiddleware)
    app.include_router(router)
    return app


app = create_app()
