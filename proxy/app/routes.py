"""HTTP 路由：/health + 8 个按站点透传端点。

统一响应 {code,msg,data}：透传端点把上游二级池响应原样返回（不缓存、
不加工任何 IP/租赁数据）；站点未配置返回 40400；上游不可达/超时返回 50200。
/health 额外展示代理层自身统计（启动时间、API 被调用次数等）。
仅统计时读取上游 body 的 ``code`` 业务码（body 已被透传解析），响应仍原样返回。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ip_pool_common.api import ErrorCode, err, ok

from .dispatcher import SiteNotFound, UpstreamError

router = APIRouter(prefix="/api/v1", tags=["proxy"])


def _record(request: Request, *, site: str | None = None, error_code: int | None = None) -> None:
    """记录一次代理层调用统计（站点转发 / 错误计数）。"""
    stats = getattr(request.app.state, "stats", None)
    if stats is None:
        return
    if site is not None:
        stats.record_site(site)
    if error_code is not None:
        stats.record_error(error_code)


async def _forward(request: Request, site: str, method: str) -> JSONResponse:
    """按站点透传请求到对应二级池，原样返回上游 {code,msg,data}。"""
    dispatcher = request.app.state.dispatcher
    json_body = None
    if method in ("POST", "PUT", "PATCH"):
        raw = await request.body()
        if raw:
            try:
                json_body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                json_body = None
    try:
        status, body = await dispatcher.forward(
            site,
            method,
            request.url.path,
            params=dict(request.query_params),
            json_body=json_body,
        )
    except SiteNotFound:
        _record(request, error_code=int(ErrorCode.NOT_FOUND))
        return JSONResponse(
            status_code=404,
            content=err(ErrorCode.NOT_FOUND, "site not configured"),
        )
    except UpstreamError:
        _record(request, error_code=int(ErrorCode.UPSTREAM_ERROR))
        return JSONResponse(
            status_code=502,
            content=err(ErrorCode.UPSTREAM_ERROR, "upstream error"),
        )
    _record(request, site=site)
    biz_code = body.get("code") if isinstance(body, dict) else None
    if status >= 400 or (isinstance(biz_code, int) and biz_code != 0):
        _record(request, error_code=biz_code if isinstance(biz_code, int) else status)
    return JSONResponse(status_code=status, content=body)


@router.get("/health")
async def health(request: Request):
    registry = request.app.state.registry
    sites = [
        {"name": r.name, "base_url": r.base_url, "target_url": r.target_url}
        for r in registry.sites()
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
    }
    return ok(data)


@router.get("/{site}/status")
async def status(site: str, request: Request):
    return await _forward(request, site, "GET")


@router.get("/{site}/count")
async def count(site: str, request: Request):
    return await _forward(request, site, "GET")


@router.get("/{site}/ips")
async def ips(site: str, request: Request):
    return await _forward(request, site, "GET")


@router.post("/{site}/ips/acquire")
async def acquire(site: str, request: Request):
    return await _forward(request, site, "POST")


@router.post("/{site}/ips/{id_}/release")
async def release(site: str, id_: int, request: Request):
    return await _forward(request, site, "POST")


@router.delete("/{site}/ips/{id_}")
async def delete(site: str, id_: int, request: Request):
    return await _forward(request, site, "DELETE")


@router.post("/{site}/ips/release-all")
async def release_all(site: str, request: Request):
    return await _forward(request, site, "POST")
