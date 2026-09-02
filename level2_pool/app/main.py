"""FastAPI 装配：lifespan 创建/关闭资源、启停后台任务，注册路由与调用计数中间件。

- 中间件只统计 /api/v1 业务端点调用次数（写入 ``app.state.stats.api_call_count``）；
- ``create_app`` 全部组件可注入（供测试），未注入的在 lifespan 内按配置创建；
- 模块级 ``app`` 供 ``uvicorn app.main:app`` 使用（自动加载 config/level2_pool.yaml）。
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

from .config import Level2Settings, load_level2_settings
from .pool import Level2Pool, ServiceStats
from .routes import router
from .syncer import Level1SyncClient, SyncTask
from .tasks import RevalidateTask, TtlSweeper
from .tester import Tester

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config", "level2_pool.yaml"
)


class V1CounterMiddleware(ApiCounterMiddleware):
    """只统计 /api/v1 业务端点的调用次数，计数写入 ``app.state.stats.api_call_count``。"""

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/v1"):
            app = scope.get("app")
            state = getattr(app, "state", None)
            if state is not None:
                stats = getattr(state, "stats", None)
                if stats is not None:
                    stats.api_call_count += 1
        await self.app(scope, receive, send)


def create_app(
    settings: Level2Settings | None = None,
    *,
    pool: Level2Pool | None = None,
    stats: ServiceStats | None = None,
    tester: Tester | None = None,
    session: aiohttp.ClientSession | None = None,
    sync_task: SyncTask | None = None,
    start_time: float | None = None,
    start_tasks: bool = True,
    configure_logging: bool = True,
) -> FastAPI:
    """装配 FastAPI 应用；组件可注入，未注入的在 lifespan 内按配置创建。

    ``configure_logging=False`` 用于多开模式：由 launcher 统一初始化 stdout 并按子池拆分文件日志，
    避免各子池重复初始化 stdout handler。
    """
    if settings is None:
        if os.path.exists(_CONFIG_PATH):
            settings = load_level2_settings(_CONFIG_PATH)
        else:
            settings = load_level2_settings()

    if configure_logging:
        setup_logging("level2_pool", level=settings.service.log_level)

    pool = pool or Level2Pool()
    stats = stats or ServiceStats()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        own_session = session is None
        active_session = (
            session if session is not None else (aiohttp.ClientSession() if start_tasks else None)
        )
        active_tester = tester if tester is not None else (
            Tester(
                target_url=settings.site.target_url,
                threshold_ms=settings.test.latency_threshold_ms,
                connect_timeout=settings.test.connect_timeout,
                concurrency=settings.test.concurrency,
            )
            if start_tasks
            else None
        )
        active_sync = sync_task
        background: list[asyncio.Task] = []
        if start_tasks:
            client = Level1SyncClient(
                settings.level1.base_url, active_session, timeout=settings.sync.timeout
            )
            active_sync = active_sync or SyncTask(
                client,
                active_tester,
                pool,
                stats,
                interval=settings.sync.interval,
                buffer_size=settings.test.buffer,
                test_workers=settings.test.workers,
            )
            background = [
                asyncio.create_task(active_sync.run()),
                asyncio.create_task(
                    RevalidateTask(pool, active_tester, settings.revalidate_interval, stats).run()
                ),
                asyncio.create_task(
                    TtlSweeper(pool, settings.ttl_sweep_interval, stats).run()
                ),
            ]
        app.state.sync_task = active_sync
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            if background:
                await asyncio.gather(*background, return_exceptions=True)
            if active_session is not None and own_session:
                await active_session.close()

    app = FastAPI(title="Level2 IP Pool", lifespan=lifespan)
    app.state.settings = settings
    app.state.pool = pool
    app.state.stats = stats
    app.state.start_time = start_time if start_time is not None else time.time()
    app.add_middleware(V1CounterMiddleware)
    app.add_middleware(BizCodeLogMiddleware)
    app.include_router(router)
    return app


app = create_app()