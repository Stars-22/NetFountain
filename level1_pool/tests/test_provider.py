"""provider.py 测试：工厂选择 / 未知类型 / 响应解析 / 异常容错 / close。"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest import mock

import aiohttp
import pytest

from app.provider import (
    BaseProvider,
    DefaultHttpProvider,
    FreeProxyProvider,
    Http91Provider,
    ProviderFactory,
    register,
)
from ip_pool_common.models import Protocol


@register("custom_probe")
class ProbeProvider(BaseProvider):
    async def pull(self, count: int):
        return []


async def test_factory_selects_subclass_by_type(mock_session, provider_cfg):
    session, _ = mock_session
    prov = ProviderFactory.create("default_http", provider_cfg, session)
    assert isinstance(prov, DefaultHttpProvider)
    assert prov.name == "default_http"


async def test_factory_unknown_type_raises(mock_session, provider_cfg):
    session, _ = mock_session
    with pytest.raises(ValueError, match="unknown provider type"):
        ProviderFactory.create("definitely_not_registered", provider_cfg, session)


async def test_factory_register_and_create_custom(provider_cfg):
    assert ProbeProvider.name == "custom_probe"
    prov = ProviderFactory.create(
        "custom_probe", provider_cfg, mock.MagicMock()
    )
    assert isinstance(prov, ProbeProvider)
    assert ProviderFactory._registry["custom_probe"] is ProbeProvider


async def test_factory_available_types_in_error(mock_session, provider_cfg):
    session, _ = mock_session
    with pytest.raises(ValueError) as exc_info:
        ProviderFactory.create("nope", provider_cfg, session)
    assert "default_http" in str(exc_info.value)
    assert "custom_probe" in str(exc_info.value)


def test_base_provider_is_abstract(provider_cfg):
    with pytest.raises(TypeError):
        BaseProvider(provider_cfg, mock.MagicMock())


async def test_pull_parses_standard_response(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(
        provider_request_url(),
        status=200,
        payload={
            "data": [
                {"ip": "1.2.3.4", "port": 8080, "protocol": "http", "region": "CN", "ttl": 120},
                {"ip": "1.2.3.5", "port": 8081},
                {"ip": "1.2.3.6", "port": "8082", "protocol": "https", "region": "", "ttl": "60"},
            ]
        },
    )
    provider = DefaultHttpProvider(provider_cfg, session)
    ips = await provider.pull(10)
    assert len(ips) == 3
    assert ips[0].ip == "1.2.3.4"
    assert ips[0].port == 8080
    assert ips[0].protocol == Protocol.HTTP
    assert ips[0].region == "CN"
    assert ips[0].ttl == 120.0
    assert ips[1].protocol == Protocol.HTTP
    assert ips[1].region is None
    assert ips[1].ttl is None
    assert ips[2].protocol == Protocol.HTTPS
    assert ips[2].port == 8082
    assert ips[2].region is None
    assert ips[2].ttl == 60.0


async def test_pull_invalid_protocol_defaults_http(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(
        provider_request_url(),
        status=200,
        payload={"data": [{"ip": "9.9.9.9", "port": 1, "protocol": "weird"}]},
    )
    provider = DefaultHttpProvider(provider_cfg, session)
    ips = await provider.pull(10)
    assert len(ips) == 1
    assert ips[0].protocol == Protocol.HTTP


async def test_pull_invalid_ttl_coerces_none(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(
        provider_request_url(),
        status=200,
        payload={"data": [{"ip": "1.2.3.4", "port": 8080, "ttl": "abc"}]},
    )
    provider = DefaultHttpProvider(provider_cfg, session)
    ips = await provider.pull(10)
    assert len(ips) == 1
    assert ips[0].ttl is None


async def test_pull_empty_data_returns_empty(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(provider_request_url(), status=200, payload={"data": []})
    provider = DefaultHttpProvider(provider_cfg, session)
    assert await provider.pull(10) == []


async def test_pull_missing_data_key_returns_empty(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(provider_request_url(), status=200, payload={"foo": "bar"})
    provider = DefaultHttpProvider(provider_cfg, session)
    assert await provider.pull(10) == []


async def test_pull_non_object_payload_returns_empty(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(provider_request_url(), status=200, body="[]", content_type="application/json")
    provider = DefaultHttpProvider(provider_cfg, session)
    assert await provider.pull(10) == []


async def test_pull_skips_malformed_items(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(
        provider_request_url(),
        status=200,
        payload={
            "data": [
                42,
                {"ip": "1.2.3.4"},
                {"ip": "", "port": 80},
                {"port": 8080},
                {"ip": "1.2.3.5", "port": "not-a-port"},
                {"ip": "   ", "port": 1},
            ]
        },
    )
    provider = DefaultHttpProvider(provider_cfg, session)
    assert await provider.pull(10) == []


async def test_pull_respects_count_limit(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    data = [{"ip": f"1.1.1.{i}", "port": 8000 + i} for i in range(1, 16)]
    m.get(provider_request_url(count=5), status=200, payload={"data": data})
    provider = DefaultHttpProvider(provider_cfg, session)
    ips = await provider.pull(5)
    assert len(ips) == 5


async def test_pull_sends_api_key_query_param(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(
        provider_request_url(api_key=provider_cfg.api_key),
        status=200,
        payload={"data": [{"ip": "1.2.3.4", "port": 80}]},
    )
    provider = DefaultHttpProvider(provider_cfg, session)
    ips = await provider.pull(10)
    assert len(ips) == 1


async def test_pull_no_api_key_omits_param(provider_cfg, provider_request_url, mock_session):
    provider_cfg.api_key = ""
    session, m = mock_session
    m.get(provider_request_url(api_key=""), status=200, payload={"data": []})
    provider = DefaultHttpProvider(provider_cfg, session)
    assert await provider.pull(10) == []


async def test_pull_500_raises(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(provider_request_url(), status=500, body=b"boom")
    provider = DefaultHttpProvider(provider_cfg, session)
    with pytest.raises(aiohttp.ClientResponseError):
        await provider.pull(10)


async def test_pull_timeout_raises(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(provider_request_url(), exception=asyncio.TimeoutError())
    provider = DefaultHttpProvider(provider_cfg, session)
    with pytest.raises(asyncio.TimeoutError):
        await provider.pull(10)


async def test_pull_connection_error_raises(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(provider_request_url(), exception=aiohttp.ClientConnectionError("refused"))
    provider = DefaultHttpProvider(provider_cfg, session)
    with pytest.raises(aiohttp.ClientConnectionError):
        await provider.pull(10)


async def test_pull_cancelled_rethrows(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(provider_request_url(), exception=asyncio.CancelledError())
    provider = DefaultHttpProvider(provider_cfg, session)
    with pytest.raises(asyncio.CancelledError):
        await provider.pull(10)


async def test_pull_invalid_json_raises(provider_cfg, provider_request_url, mock_session):
    session, m = mock_session
    m.get(provider_request_url(), status=200, body="not json{", content_type="application/json")
    provider = DefaultHttpProvider(provider_cfg, session)
    with pytest.raises(ValueError):
        await provider.pull(10)


async def test_close_idempotent(mock_session, provider_cfg):
    session, _ = mock_session
    provider = DefaultHttpProvider(provider_cfg, session)
    await provider.close()
    await provider.close()


# ---------------------------------------------------------------------------
# Http91Provider
# ---------------------------------------------------------------------------


def _http91_payload(items: list[dict], code: int = 0, msg: str = "OK") -> dict:
    return {
        "code": code,
        "msg": msg,
        "data": {
            "count": len(items),
            "filter_count": 0,
            "surplus_quantity": 0,
            "proxy_list": items,
        },
    }


async def test_factory_creates_http91(mock_session, http91_cfg):
    session, _ = mock_session
    prov = ProviderFactory.create("http91", http91_cfg, session)
    assert isinstance(prov, Http91Provider)
    assert prov.name == "http91"


async def test_http91_pull_parses_expire_time_to_ttl(
    mock_session, http91_cfg, http91_request_url
):
    session, m = mock_session
    m.get(
        http91_request_url(),
        status=200,
        payload=_http91_payload(
            [
                {"ip": "183.167.165.238", "port": 38587, "expire_time": "2026-08-28 17:03:06"},
                {"ip": "49.71.41.98", "port": 46019, "expire_time": "2026-08-28 17:02:56"},
            ]
        ),
    )
    provider = Http91Provider(http91_cfg, session)
    ips = await provider.pull(10)
    assert len(ips) == 2
    assert ips[0].ip == "183.167.165.238"
    assert ips[0].port == 38587
    assert ips[0].protocol == Protocol.HTTP
    assert ips[1].ttl is not None


def test_http91_parse_ttl_remaining_seconds(http91_cfg):
    provider = Http91Provider(http91_cfg, mock.MagicMock())
    now = datetime.strptime("2026-08-28 17:00:00", "%Y-%m-%d %H:%M:%S").timestamp()
    ips = provider._parse(
        _http91_payload(
            [{"ip": "1.2.3.4", "port": 8080, "expire_time": "2026-08-28 17:03:06"}]
        ),
        10,
        now=now,
    )
    assert len(ips) == 1
    assert ips[0].ttl == 186.0


def test_http91_parse_code_nonzero_returns_empty(http91_cfg):
    provider = Http91Provider(http91_cfg, mock.MagicMock())
    payload = {"code": 104, "msg": "未检索到满足要求的代理IP", "data": None}
    assert provider._parse(payload, 10) == []


def test_http91_parse_respects_count_limit(http91_cfg):
    provider = Http91Provider(http91_cfg, mock.MagicMock())
    items = [{"ip": f"1.1.1.{i}", "port": 8000 + i} for i in range(1, 16)]
    ips = provider._parse(_http91_payload(items), 5)
    assert len(ips) == 5


def test_http91_parse_missing_expire_time_ttl_none(http91_cfg):
    provider = Http91Provider(http91_cfg, mock.MagicMock())
    ips = provider._parse(
        _http91_payload([{"ip": "1.2.3.4", "port": 8080}]),
        10,
    )
    assert len(ips) == 1
    assert ips[0].ttl is None


def test_http91_parse_invalid_expire_time_ttl_none(http91_cfg):
    provider = Http91Provider(http91_cfg, mock.MagicMock())
    ips = provider._parse(
        _http91_payload(
            [{"ip": "1.2.3.4", "port": 8080, "expire_time": "not-a-time"}]
        ),
        10,
    )
    assert len(ips) == 1
    assert ips[0].ttl is None


def test_http91_parse_skips_malformed_items(http91_cfg):
    provider = Http91Provider(http91_cfg, mock.MagicMock())
    ips = provider._parse(
        _http91_payload(
            [
                {"ip": "1.2.3.4"},
                {"port": 8080},
                {"ip": "   ", "port": 1},
                {"ip": "1.2.3.5", "port": "not-a-port"},
            ]
        ),
        10,
    )
    assert ips == []


def test_http91_protocol_socks(http91_cfg):
    http91_cfg.protocol = 2
    provider = Http91Provider(http91_cfg, mock.MagicMock())
    ips = provider._parse(
        _http91_payload([{"ip": "1.2.3.4", "port": 8080}]),
        10,
    )
    assert len(ips) == 1
    assert ips[0].protocol == Protocol.SOCKS5


async def test_http91_pull_500_raises(mock_session, http91_cfg, http91_request_url):
    session, m = mock_session
    m.get(http91_request_url(), status=500, body=b"boom")
    provider = Http91Provider(http91_cfg, session)
    with pytest.raises(aiohttp.ClientResponseError):
        await provider.pull(10)


async def test_http91_pull_timeout_raises(mock_session, http91_cfg, http91_request_url):
    session, m = mock_session
    m.get(http91_request_url(), exception=asyncio.TimeoutError())
    provider = Http91Provider(http91_cfg, session)
    with pytest.raises(asyncio.TimeoutError):
        await provider.pull(10)


async def test_http91_pull_connection_error_raises(
    mock_session, http91_cfg, http91_request_url
):
    session, m = mock_session
    m.get(http91_request_url(), exception=aiohttp.ClientConnectionError("refused"))
    provider = Http91Provider(http91_cfg, session)
    with pytest.raises(aiohttp.ClientConnectionError):
        await provider.pull(10)


async def test_http91_pull_cancelled_rethrows(
    mock_session, http91_cfg, http91_request_url
):
    session, m = mock_session
    m.get(http91_request_url(), exception=asyncio.CancelledError())
    provider = Http91Provider(http91_cfg, session)
    with pytest.raises(asyncio.CancelledError):
        await provider.pull(10)


def test_http91_parse_non_object_payload(http91_cfg):
    provider = Http91Provider(http91_cfg, mock.MagicMock())
    assert provider._parse([], 10) == []


def test_http91_parse_data_not_object(http91_cfg):
    provider = Http91Provider(http91_cfg, mock.MagicMock())
    assert provider._parse({"code": 0, "data": "oops"}, 10) == []


def test_http91_parse_missing_proxy_list(http91_cfg):
    provider = Http91Provider(http91_cfg, mock.MagicMock())
    assert provider._parse({"code": 0, "data": {"count": 0}}, 10) == []


def test_http91_parse_skips_non_dict_item(http91_cfg):
    provider = Http91Provider(http91_cfg, mock.MagicMock())
    ips = provider._parse(
        _http91_payload([42, {"ip": "1.2.3.4", "port": 8080}]),
        10,
    )
    assert len(ips) == 1
    assert ips[0].ip == "1.2.3.4"


def test_http91_parse_numeric_expire_time(http91_cfg):
    provider = Http91Provider(http91_cfg, mock.MagicMock())
    ips = provider._parse(
        _http91_payload([{"ip": "1.2.3.4", "port": 8080, "expire_time": "1800"}]),
        10,
        now=0.0,
    )
    assert len(ips) == 1
    assert ips[0].ttl == 1800.0


# ---------------------------------------------------------------------------
# freeproxy（zdopen）
# ---------------------------------------------------------------------------


def _freeproxy_payload(items: list[dict], code: str = "10001", msg: str = "获取成功") -> dict:
    return {
        "code": code,
        "msg": msg,
        "data": {"count": len(items), "proxy_list": items},
    }


async def test_factory_creates_freeproxy(mock_session, freeproxy_cfg):
    session, _ = mock_session
    prov = ProviderFactory.create("freeproxy", freeproxy_cfg, session)
    assert isinstance(prov, FreeProxyProvider)
    assert prov.name == "freeproxy"


async def test_freeproxy_pull_parses_items(
    mock_session, freeproxy_cfg, freeproxy_request_url
):
    session, m = mock_session
    m.get(
        freeproxy_request_url(),
        status=200,
        payload=_freeproxy_payload(
            [
                {"ip": "203.25.208.163", "port": 1100, "adr": "广东省 电信",
                 "protocol": "socks5", "level": "高匿"},
                {"ip": "111.79.111.126", "port": "3128", "adr": "江西省抚州市 电信",
                 "protocol": "http", "level": "未知"},
                {"ip": "120.26.104.146", "port": 9098,
                 "protocol": "https", "level": "高匿"},
                {"ip": "1.2.3.4", "port": 7000, "adr": "   ", "protocol": "socks4"},
            ]
        ),
    )
    provider = FreeProxyProvider(freeproxy_cfg, session)
    ips = await provider.pull(10)
    assert len(ips) == 4
    assert ips[0].ip == "203.25.208.163"
    assert ips[0].port == 1100
    assert ips[0].protocol == Protocol.SOCKS5
    assert ips[0].region == "广东省 电信"
    assert ips[0].ttl is None
    assert ips[1].port == 3128
    assert ips[1].protocol == Protocol.HTTP
    assert ips[2].protocol == Protocol.HTTPS
    assert ips[2].region is None
    assert ips[3].protocol == Protocol.SOCKS4
    assert ips[3].region is None


async def test_freeproxy_pull_protocol_type_in_url(
    mock_session, freeproxy_cfg, freeproxy_request_url
):
    session, m = mock_session
    freeproxy_cfg.protocol_type = 2
    m.get(
        freeproxy_request_url(protocol_type=2),
        status=200,
        payload=_freeproxy_payload(
            [{"ip": "1.2.3.4", "port": 1080, "protocol": "socks4"}]
        ),
    )
    provider = FreeProxyProvider(freeproxy_cfg, session)
    ips = await provider.pull(10)
    assert len(ips) == 1
    assert ips[0].protocol == Protocol.SOCKS4


def test_freeproxy_params_defaults(freeproxy_cfg):
    provider = FreeProxyProvider(freeproxy_cfg, mock.MagicMock())
    params = provider._params(10)
    assert params["app_id"] == freeproxy_cfg.trade_no
    assert params["akey"] == freeproxy_cfg.api_key
    assert params["count"] == "10"
    assert params["dalu"] == "1"
    assert params["return_type"] == "3"
    assert "protocol_type" not in params


def test_freeproxy_params_clamp_count_to_100(freeproxy_cfg):
    provider = FreeProxyProvider(freeproxy_cfg, mock.MagicMock())
    assert provider._params(200)["count"] == "100"


def test_freeproxy_params_include_protocol_type_when_positive(freeproxy_cfg):
    freeproxy_cfg.protocol_type = 3
    provider = FreeProxyProvider(freeproxy_cfg, mock.MagicMock())
    assert provider._params(10)["protocol_type"] == "3"


def test_freeproxy_parse_code_non_10001_returns_empty(freeproxy_cfg):
    provider = FreeProxyProvider(freeproxy_cfg, mock.MagicMock())
    payload = {"code": "12009", "msg": "该参数条件下当前没有任何代理IP"}
    assert provider._parse(payload, 10) == []


def test_freeproxy_parse_numeric_code_10001_ok(freeproxy_cfg):
    provider = FreeProxyProvider(freeproxy_cfg, mock.MagicMock())
    payload = {"code": 10001, "msg": "获取成功",
               "data": {"proxy_list": [{"ip": "1.2.3.4", "port": 8080}]}}
    ips = provider._parse(payload, 10)
    assert len(ips) == 1
    assert ips[0].ip == "1.2.3.4"


def test_freeproxy_parse_respects_count_limit(freeproxy_cfg):
    provider = FreeProxyProvider(freeproxy_cfg, mock.MagicMock())
    items = [{"ip": f"1.1.1.{i}", "port": 8000 + i} for i in range(1, 16)]
    ips = provider._parse(_freeproxy_payload(items), 5)
    assert len(ips) == 5


def test_freeproxy_parse_skips_malformed_items(freeproxy_cfg):
    provider = FreeProxyProvider(freeproxy_cfg, mock.MagicMock())
    ips = provider._parse(
        _freeproxy_payload(
            [
                {"ip": "1.2.3.4"},                      # 缺端口
                {"port": 8080},                         # 缺 ip
                {"ip": "   ", "port": 1},               # ip 空白
                {"ip": "1.2.3.5", "port": "not-a-port"},  # 非法端口
                42,                                     # 非 dict
                {"ip": "1.2.3.6", "port": 8082},
            ]
        ),
        10,
    )
    assert len(ips) == 1
    assert ips[0].ip == "1.2.3.6"


def test_freeproxy_parse_invalid_protocol_defaults_http(freeproxy_cfg):
    provider = FreeProxyProvider(freeproxy_cfg, mock.MagicMock())
    ips = provider._parse(
        _freeproxy_payload([{"ip": "1.2.3.4", "port": 8080, "protocol": "weird"}]),
        10,
    )
    assert len(ips) == 1
    assert ips[0].protocol == Protocol.HTTP


async def test_freeproxy_pull_500_raises(
    mock_session, freeproxy_cfg, freeproxy_request_url
):
    session, m = mock_session
    m.get(freeproxy_request_url(), status=500, body=b"boom")
    provider = FreeProxyProvider(freeproxy_cfg, session)
    with pytest.raises(aiohttp.ClientResponseError):
        await provider.pull(10)


async def test_freeproxy_pull_timeout_raises(
    mock_session, freeproxy_cfg, freeproxy_request_url
):
    session, m = mock_session
    m.get(freeproxy_request_url(), exception=asyncio.TimeoutError())
    provider = FreeProxyProvider(freeproxy_cfg, session)
    with pytest.raises(asyncio.TimeoutError):
        await provider.pull(10)


async def test_freeproxy_pull_connection_error_raises(
    mock_session, freeproxy_cfg, freeproxy_request_url
):
    session, m = mock_session
    m.get(freeproxy_request_url(), exception=aiohttp.ClientConnectionError("refused"))
    provider = FreeProxyProvider(freeproxy_cfg, session)
    with pytest.raises(aiohttp.ClientConnectionError):
        await provider.pull(10)


async def test_freeproxy_pull_cancelled_rethrows(
    mock_session, freeproxy_cfg, freeproxy_request_url
):
    session, m = mock_session
    m.get(freeproxy_request_url(), exception=asyncio.CancelledError())
    provider = FreeProxyProvider(freeproxy_cfg, session)
    with pytest.raises(asyncio.CancelledError):
        await provider.pull(10)


def test_freeproxy_parse_non_object_payload(freeproxy_cfg):
    provider = FreeProxyProvider(freeproxy_cfg, mock.MagicMock())
    assert provider._parse([], 10) == []


def test_freeproxy_parse_data_not_object(freeproxy_cfg):
    provider = FreeProxyProvider(freeproxy_cfg, mock.MagicMock())
    assert provider._parse({"code": "10001", "data": "oops"}, 10) == []


def test_freeproxy_parse_missing_proxy_list(freeproxy_cfg):
    provider = FreeProxyProvider(freeproxy_cfg, mock.MagicMock())
    assert provider._parse({"code": "10001", "data": {"count": 0}}, 10) == []
