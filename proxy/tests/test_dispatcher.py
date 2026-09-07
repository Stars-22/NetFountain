"""dispatcher.py 测试：路径剥离、method/query/body 透传、响应原样返回、错误处理、故障隔离。

覆盖测试计划书 PX-DIS-001 ~ 008。
"""
from __future__ import annotations

import asyncio

import aiohttp
import pytest

from app.core.dispatcher import SiteNotFound, UpstreamError, strip_site as _strip_site

PAYLOAD_A = {"code": 0, "msg": "ok", "data": {"site": "site_a", "pool": ["10.0.0.1"]}}
PAYLOAD_B = {"code": 0, "msg": "ok", "data": {"site": "site_b", "pool": ["10.0.0.9"]}}


def _json_calls(aio_mock, method: str, url_prefix: str):
    """返回 aio_mock 中转发到 url_prefix 的调用（携带 json body 的 kwargs）。"""
    out = []
    for (m, u), calls in aio_mock.requests.items():
        if m.upper() == method.upper() and str(u).startswith(url_prefix):
            out.extend(calls)
    return out


# PX-DIS-001 路径剥离转发
async def test_path_stripping(dispatcher, aio_mock):
    aio_mock.get(
        "http://127.0.0.1:8001/api/v1/ips/acquire", payload=PAYLOAD_A, status=200
    )
    status, body = await dispatcher.forward(
        "site_a", "GET", "/api/v1/site_a/ips/acquire"
    )
    assert status == 200
    assert body == PAYLOAD_A


# PX-DIS-001 路径剥离（defensive 分支）
def test_strip_site_defensive_branches():
    assert _strip_site("site_a", "/other/path") == "/other/path"  # 非 /api/v1 前缀
    assert _strip_site("site_a", "/api/v1/site_a") == "/api/v1/"  # 无后续段
    assert _strip_site("site_a", "/api/v1/site_b/status") == "/api/v1/site_b/status"  # 首段不匹配


# PX-DIS-002 method 透传
async def test_method_passthrough(dispatcher, aio_mock):
    aio_mock.get("http://127.0.0.1:8001/api/v1/ips", payload=PAYLOAD_A, status=200)
    status, body = await dispatcher.forward("site_a", "GET", "/api/v1/site_a/ips")
    assert status == 200
    assert body == PAYLOAD_A

    aio_mock.post(
        "http://127.0.0.1:8001/api/v1/ips/acquire", payload=PAYLOAD_A, status=200
    )
    status, body = await dispatcher.forward(
        "site_a", "POST", "/api/v1/site_a/ips/acquire"
    )
    assert status == 200
    assert body == PAYLOAD_A

    aio_mock.delete(
        "http://127.0.0.1:8001/api/v1/ips/42", payload=PAYLOAD_A, status=200
    )
    status, body = await dispatcher.forward("site_a", "DELETE", "/api/v1/site_a/ips/42")
    assert status == 200
    assert body == PAYLOAD_A


# PX-DIS-003 query/body 透传
async def test_query_params_passthrough(dispatcher, aio_mock):
    aio_mock.get(
        "http://127.0.0.1:8001/api/v1/ips?page=1&size=20",
        payload=PAYLOAD_A,
        status=200,
    )
    status, body = await dispatcher.forward(
        "site_a", "GET", "/api/v1/site_a/ips", params={"page": "1", "size": "20"}
    )
    assert status == 200
    assert body == PAYLOAD_A


async def test_json_body_passthrough(dispatcher, aio_mock):
    aio_mock.post(
        "http://127.0.0.1:8001/api/v1/ips/acquire", payload=PAYLOAD_A, status=200
    )
    body = {"preferred": "http", "count": 3}
    status, resp = await dispatcher.forward(
        "site_a", "POST", "/api/v1/site_a/ips/acquire", json_body=body
    )
    assert status == 200
    calls = _json_calls(aio_mock, "POST", "http://127.0.0.1:8001/api/v1/ips/acquire")
    assert calls and calls[-1].kwargs["json"] == body


# PX-DIS-004 响应原样返回（不解析、不加工）
async def test_response_passthrough_unmodified(dispatcher, aio_mock):
    upstream = {"code": 40400, "msg": "record not found", "data": None}
    aio_mock.get("http://127.0.0.1:8001/api/v1/ips/999", payload=upstream, status=200)
    status, body = await dispatcher.forward("site_a", "GET", "/api/v1/site_a/ips/999")
    assert status == 200
    assert body == upstream


async def test_upstream_status_code_passthrough(dispatcher, aio_mock):
    aio_mock.get(
        "http://127.0.0.1:8001/api/v1/status", payload=PAYLOAD_A, status=503
    )
    status, body = await dispatcher.forward("site_a", "GET", "/api/v1/site_a/status")
    assert status == 503
    assert body == PAYLOAD_A


# PX-DIS-005 站点未配置
async def test_unknown_site_raises(dispatcher):
    with pytest.raises(SiteNotFound) as excinfo:
        await dispatcher.forward("nope", "GET", "/api/v1/nope/status")
    assert "site not configured" in str(excinfo.value)
    assert excinfo.value.site == "nope"


# PX-DIS-006 上游不可达
async def test_upstream_connection_error(dispatcher, aio_mock):
    aio_mock.get(
        "http://127.0.0.1:8001/api/v1/status",
        exception=ConnectionError("connection refused"),
    )
    with pytest.raises(UpstreamError):
        await dispatcher.forward("site_a", "GET", "/api/v1/site_a/status")


async def test_upstream_aiohttp_connection_error(dispatcher, aio_mock):
    aio_mock.get(
        "http://127.0.0.1:8001/api/v1/status",
        exception=aiohttp.ClientConnectionError("refused"),
    )
    with pytest.raises(UpstreamError):
        await dispatcher.forward("site_a", "GET", "/api/v1/site_a/status")


# PX-DIS-007 上游超时（不挂起）
async def test_upstream_timeout(dispatcher, aio_mock):
    aio_mock.get("http://127.0.0.1:8001/api/v1/status", timeout=True)
    with pytest.raises(UpstreamError):
        await dispatcher.forward("site_a", "GET", "/api/v1/site_a/status")


async def test_upstream_non_json_response(dispatcher, aio_mock):
    aio_mock.get(
        "http://127.0.0.1:8001/api/v1/status",
        body="<html>oops</html>",
        content_type="text/html",
        status=200,
    )
    with pytest.raises(UpstreamError):
        await dispatcher.forward("site_a", "GET", "/api/v1/site_a/status")


# PX-DIS-008 单站点故障不影响其它站点
async def test_single_site_failure_isolated(dispatcher, aio_mock):
    aio_mock.get(
        "http://127.0.0.1:8001/api/v1/ips",
        exception=ConnectionError("site_a down"),
    )
    aio_mock.get("http://127.0.0.1:8002/api/v1/ips", payload=PAYLOAD_B, status=200)
    with pytest.raises(UpstreamError):
        await dispatcher.forward("site_a", "GET", "/api/v1/site_a/ips")
    status, body = await dispatcher.forward("site_b", "GET", "/api/v1/site_b/ips")
    assert status == 200
    assert body == PAYLOAD_B


# 并发转发不串扰（透传层并发安全）
async def test_concurrent_forwards_no_crosstalk(dispatcher, aio_mock):
    aio_mock.get(
        "http://127.0.0.1:8001/api/v1/ips", payload=PAYLOAD_A, status=200, repeat=True
    )
    aio_mock.get(
        "http://127.0.0.1:8002/api/v1/ips", payload=PAYLOAD_B, status=200, repeat=True
    )

    async def call(site: str):
        _, body = await dispatcher.forward(site, "GET", f"/api/v1/{site}/ips")
        return site, body["data"]["site"]

    results = await asyncio.gather(
        *[call("site_a" if i % 2 == 0 else "site_b") for i in range(50)]
    )
    for requested, returned in results:
        assert requested == returned