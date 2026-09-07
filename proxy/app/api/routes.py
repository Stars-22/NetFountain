"""HTTP 路由装配：/health 实时聚合端点 + 按站点透传端点表注册。

/health 额外实时聚合一级池与各站点二级池的 ``/status`` 信息（``pools``），
下游不可达时该条目标记为 error。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import aiohttp
from fastapi import APIRouter, Request

from ip_pool_common.api import ok

from .passthrough import register_passthrough_routes

router = APIRouter(prefix="/api/v1", tags=["proxy"])

__all__ = ["router"]

register_passthrough_routes(router)


async def _fetch_pool_status(
    session: aiohttp.ClientSession, base_url: str, timeout: float
) -> dict:
    """GET ``{base_url}/api/v1/status``，返回其 ``data``；失败时返回 ``{"error": ...}``。"""
    url = base_url.rstrip("/") + "/api/v1/status"
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status != 200:
                return {"error": f"HTTP {resp.status}"}
            body = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        return {"error": str(exc) or type(exc).__name__}
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else {"error": "no status data"}


@router.get("/health")
async def health(request: Request):
    registry = request.app.state.registry
    session = request.app.state.dispatcher.session
    settings = request.app.state.settings
    routes = registry.sites()

    level1_url = settings.level1.base_url
    level1_status, *site_statuses = await asyncio.gather(
        _fetch_pool_status(session, level1_url, settings.level1.timeout),
        *(
            _fetch_pool_status(session, r.base_url, settings.level1.timeout)
            for r in routes
        ),
    )

    sites = [
        {"name": r.name, "base_url": r.base_url, "target_url": r.target_url}
        for r in routes
    ]
    pool_sites = [
        {"name": r.name, "base_url": r.base_url, "status": status}
        for r, status in zip(routes, site_statuses)
    ]
    started_at = datetime.fromtimestamp(
        request.app.state.start_time, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "status": "ok",
        "started_at": started_at,
        "uptime": round(time.time() - request.app.state.start_time, 3),
        "stats": request.app.state.stats.snapshot(),
        "sites": sites,
        "pools": {
            "level1": {"base_url": level1_url, "status": level1_status},
            "sites": pool_sites,
        },
    }
    return ok(data)
