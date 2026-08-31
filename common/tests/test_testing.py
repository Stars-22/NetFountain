"""testing.py 测试：代理可达性、站点连通、批量并发（网络全部打桩）。"""
from __future__ import annotations

import asyncio

from aiohttp_socks import ProxyError

from ip_pool_common.models import build_proxy_url
from ip_pool_common.testing import (
    _is_legal_proxy_reply,
    batch_test,
    classify_test_error,
    proxy_reachability_test,
    proxy_reachability_test_detailed,
    site_test,
    site_test_detailed,
)

HTTP_PROXY = "http://1.2.3.4:8080"
SITE_URL = "https://example.com/"


async def test_http_proxy_ok(http_proxy_server):
    url = await http_proxy_server("ok")
    ok, latency = await proxy_reachability_test(url)
    assert ok is True
    assert latency >= 0


async def test_http_proxy_407_still_ok(http_proxy_server):
    url = await http_proxy_server("auth")
    ok, _ = await proxy_reachability_test(url)
    assert ok is True


async def test_http_proxy_502_legal_reply(http_proxy_server):
    url = await http_proxy_server("refuse")
    ok, _ = await proxy_reachability_test(url)
    assert ok is True


async def test_http_proxy_connection_refused(http_proxy_server):
    url = await http_proxy_server("refused")
    ok, _ = await proxy_reachability_test(url)
    assert ok is False


async def test_http_proxy_timeout_no_hang(http_proxy_server):
    url = await http_proxy_server("timeout")
    ok, _ = await proxy_reachability_test(url, timeout=0.5)
    assert ok is False


async def test_http_proxy_reset_no_reply(http_proxy_server):
    url = await http_proxy_server("reset")
    ok, _ = await proxy_reachability_test(url, timeout=2.0)
    assert ok is False


async def test_socks4_ok(socks_server):
    url = await socks_server(version="socks4", mode="normal")
    ok, _ = await proxy_reachability_test(url)
    assert ok is True


async def test_socks5_ok(socks_server):
    url = await socks_server(version="socks5", mode="normal")
    ok, _ = await proxy_reachability_test(url)
    assert ok is True


async def test_socks_refused(socks_server):
    for version in ("socks4", "socks5"):
        url = await socks_server(version=version, mode="refuse")
        ok, _ = await proxy_reachability_test(url)
        assert ok is False


async def test_socks_timeout(socks_server):
    url = await socks_server(version="socks5", mode="timeout")
    ok, _ = await proxy_reachability_test(url, timeout=0.3)
    assert ok is False


async def test_invalid_proxy_url():
    ok, latency = await proxy_reachability_test("")
    assert ok is False
    assert latency == 0.0
    ok, _ = await proxy_reachability_test("ftp://host:21")
    assert ok is False


def test_is_legal_proxy_reply():
    assert _is_legal_proxy_reply(ProxyError("502", error_code=502)) is True
    assert _is_legal_proxy_reply(ProxyError("407", error_code=407)) is True
    assert _is_legal_proxy_reply(ProxyError("refused", error_code=91)) is False
    assert _is_legal_proxy_reply(ProxyError("malformed")) is False


async def test_site_test_ok(aio_mock):
    m = aio_mock
    m.get(SITE_URL, status=200, body=b"<html>ok</html>")
    ok, latency = await site_test(HTTP_PROXY, SITE_URL)
    assert ok is True
    assert latency >= 0


async def test_site_test_4xx_is_reachable(aio_mock):
    m = aio_mock
    m.get(SITE_URL, status=404, body=b"not found")
    ok, _ = await site_test(HTTP_PROXY, SITE_URL)
    assert ok is True


async def test_site_test_5xx_fails(aio_mock):
    m = aio_mock
    m.get(SITE_URL, status=502, body=b"bad gateway")
    ok, _ = await site_test(HTTP_PROXY, SITE_URL)
    assert ok is False


async def test_site_test_timeout_fails(aio_mock):
    m = aio_mock
    m.get(SITE_URL, exception=asyncio.TimeoutError())
    ok, _ = await site_test(HTTP_PROXY, SITE_URL, timeout=0.5)
    assert ok is False


async def test_site_test_connection_error(aio_mock):
    m = aio_mock
    m.get(SITE_URL, exception=ConnectionError("refused"))
    ok, _ = await site_test(HTTP_PROXY, SITE_URL)
    assert ok is False


async def test_site_test_with_provided_session(mock_session):
    session, m = mock_session
    m.get(SITE_URL, status=200, body=b"ok")
    ok, _ = await site_test(HTTP_PROXY, SITE_URL, session=session)
    assert ok is True


async def test_site_test_invalid_proxy():
    ok, latency = await site_test("", SITE_URL)
    assert ok is False
    assert latency == 0.0


async def test_classify_test_error():
    assert classify_test_error(asyncio.TimeoutError()) == "timeout"
    assert classify_test_error(TimeoutError("t")) == "timeout"
    assert classify_test_error(ConnectionError("refused")) == "connect"
    assert classify_test_error(OSError("conn")) == "connect"
    assert classify_test_error(ValueError("bad url")) == "invalid_proxy"
    assert classify_test_error(RuntimeError("boom")) == "exception"


async def test_site_test_detailed_timeout_reason(aio_mock):
    aio_mock.get(SITE_URL, exception=asyncio.TimeoutError())
    ok, _, reason = await site_test_detailed(HTTP_PROXY, SITE_URL, timeout=0.5)
    assert ok is False
    assert reason == "timeout"


async def test_site_test_detailed_5xx_reason(aio_mock):
    aio_mock.get(SITE_URL, status=502, body=b"bad gateway")
    ok, _, reason = await site_test_detailed(HTTP_PROXY, SITE_URL)
    assert ok is False
    assert reason == "http_5xx"


async def test_site_test_detailed_connect_reason(aio_mock):
    aio_mock.get(SITE_URL, exception=ConnectionError("refused"))
    ok, _, reason = await site_test_detailed(HTTP_PROXY, SITE_URL)
    assert ok is False
    assert reason == "connect"


async def test_site_test_detailed_invalid_proxy_reason():
    ok, _, reason = await site_test_detailed("", SITE_URL)
    assert ok is False
    assert reason == "invalid_proxy"


async def test_proxy_reachability_detailed_ok_reason(http_proxy_server):
    url = await http_proxy_server("ok")
    ok, _, reason = await proxy_reachability_test_detailed(url)
    assert ok is True
    assert reason is None


async def test_proxy_reachability_detailed_refused_reason(http_proxy_server):
    url = await http_proxy_server("refused")
    ok, _, reason = await proxy_reachability_test_detailed(url)
    assert ok is False
    assert reason == "connect"


async def test_proxy_reachability_detailed_timeout_reason(http_proxy_server):
    url = await http_proxy_server("timeout")
    ok, _, reason = await proxy_reachability_test_detailed(url, timeout=0.5)
    assert ok is False
    assert reason == "timeout"


async def test_proxy_reachability_detailed_reject_reason(socks_server):
    url = await socks_server(version="socks5", mode="refuse")
    ok, _, reason = await proxy_reachability_test_detailed(url)
    assert ok is False
    assert reason == "proxy_reject"


async def test_proxy_reachability_detailed_invalid_url_reason():
    ok, _, reason = await proxy_reachability_test_detailed("")
    assert ok is False
    assert reason == "invalid_proxy"


async def test_batch_test_filters_and_preserves_order():
    items = [1, 2, 3, 4, 5, 6]

    async def _fn(item):
        return (item % 2 == 0, float(item))

    result = await batch_test(items, _fn, concurrency=4)
    assert result == [2, 4, 6]


async def test_batch_test_concurrency_cap():
    active = 0
    max_active = 0

    async def _fn(_):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return True, 1.0

    await batch_test(list(range(10)), _fn, concurrency=2)
    assert max_active <= 2


async def test_batch_test_empty_input():
    result = await batch_test([], lambda _: _noop())
    assert result == []


async def test_batch_test_exception_item_excluded():
    async def _fn(item):
        if item == 2:
            raise RuntimeError("boom")
        return item != 3, 0.0

    result = await batch_test([1, 2, 3, 4], _fn)
    assert result == [1, 4]


async def test_batch_test_zero_concurrency_clamped():
    async def _fn(item):
        return item == 1, 0.0

    result = await batch_test([1, 2], _fn, concurrency=0)
    assert result == [1]


async def _noop():
    return False, 0.0