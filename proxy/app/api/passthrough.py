"""按站点透传端点：声明式路由表 + 工厂注册（开闭扩展点）。

统一响应 {code,msg,data}：透传端点把上游二级池响应原样返回（不缓存、
不加工任何 IP/租赁数据）；站点未配置返回 40400；上游不可达/超时返回 50200。
代理层只统计自身活动（调用/来源/站点转发/自身错误 40400、50200），不统计
任何二级池业务信息（如上游业务码）。

开闭原则：新增透传端点只需在 ``PASSTHROUGH_ROUTES`` 表中加一行
``(path, method)``，无需修改任何分发逻辑。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ip_pool_common.api import ErrorCode, err

from ..core.dispatcher import SiteNotFound, UpstreamError

__all__ = ["PASSTHROUGH_ROUTES", "register_passthrough_routes"]

#: 透传端点表：``(路径模板, HTTP方法)``；``{site}`` 为站点段，``{id_}`` 为记录 id
PASSTHROUGH_ROUTES: list[tuple[str, str]] = [
    ("/{site}/status", "GET"),
    ("/{site}/count", "GET"),
    ("/{site}/ips", "GET"),
    ("/{site}/ips/acquire", "POST"),
    ("/{site}/ips/acquire-batch", "POST"),
    ("/{site}/ips/{id_}/release", "POST"),
    ("/{site}/ips/{id_}", "DELETE"),
    ("/{site}/ips/release-all", "POST"),
]


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
    return JSONResponse(status_code=status, content=body)


def _make_endpoint(method: str, has_id: bool):
    """为一条透传路由生成端点函数（显式声明路径参数供 FastAPI 识别）。"""
    if has_id:

        async def endpoint(request: Request, site: str, id_: int):
            return await _forward(request, site, method)

    else:

        async def endpoint(request: Request, site: str):
            return await _forward(request, site, method)

    return endpoint


def register_passthrough_routes(router: APIRouter) -> int:
    """把 ``PASSTHROUGH_ROUTES`` 表中的全部端点注册到 router，返回注册数量。"""
    count = 0
    for path, method in PASSTHROUGH_ROUTES:
        router.add_api_route(
            path,
            _make_endpoint(method, "{id_}" in path),
            methods=[method],
            name=f"forward_{method.lower()}_{path.strip('/').replace('/', '_')}",
        )
        count += 1
    return count
