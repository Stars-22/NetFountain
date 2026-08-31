"""tester.py 测试：site_filter 阈值 / revalidate 代理可达性 / 超时处理。

覆盖测试计划书 L2-TEST-001 ~ 003。
"""
from __future__ import annotations

import logging
from unittest import mock

from ip_pool_common.models import IpRecord


def _ip(make_ip, idx):
    return make_ip(idx)


async def test_site_filter_threshold(make_ip, tester_factory):
    """<2000ms 通过，≥2000ms 不通过。"""

    def _site(rec: IpRecord):
        lat = {"10.0.0.1": 100.0, "10.0.0.2": 2000.0, "10.0.0.3": 1999.0}[rec.ip]
        return True, lat

    tester = tester_factory(site_fn=_site)
    result = await tester.site_filter([_ip(make_ip, 1), _ip(make_ip, 2), _ip(make_ip, 3)])
    assert [r.ip for r in result] == ["10.0.0.1", "10.0.0.3"]
    assert [r.latency_ms for r in result] == [100.0, 1999.0]
    assert result[0].proxy_url == "http://10.0.0.1:8001"


async def test_site_filter_timeout_fails(make_ip, tester_factory):
    """站点超时/异常 → 判失败，不悬挂。"""
    calls = 0

    async def _slow(rec):
        nonlocal calls
        calls += 1
        raise TimeoutError("timeout")

    tester = tester_factory(site_fn=_slow)
    result = await tester.site_filter([_ip(make_ip, 1), _ip(make_ip, 2)])
    assert result == []
    assert calls == 2


async def test_site_filter_uses_real_site_test(make_ip, tester_factory):
    """未注入 site_fn 时调用真实 site_test（经代理访问目标站点）。"""
    tester = tester_factory(site_fn=None, target_url="http://www.baidu.com")
    with mock.patch(
        "app.tester.site_test_detailed",
        new=mock.AsyncMock(return_value=(True, 50.0, None)),
    ) as mocked:
        result = await tester.site_filter([_ip(make_ip, 1)])
    mocked.assert_called_once_with(
        "http://10.0.0.1:8001", "http://www.baidu.com", timeout=1.0
    )
    assert len(result) == 1
    assert result[0].latency_ms == 50.0


async def test_site_filter_empty(make_ip, tester_factory):
    tester = tester_factory()
    assert await tester.site_filter([]) == []


async def test_site_filter_ok_false_excluded(make_ip, tester_factory):
    def _site(rec):
        return False, 100.0

    tester = tester_factory(site_fn=_site)
    assert await tester.site_filter([_ip(make_ip, 1)]) == []


async def test_site_filter_threshold_edge(make_ip, tester_factory):
    """threshold=2000 时 1999 通过、2000 不通过（严格 <）。"""

    def _site(rec):
        return True, {"10.0.0.1": 1999.0, "10.0.0.2": 2000.0}[rec.ip]

    tester = tester_factory(site_fn=_site, threshold=2000)
    result = await tester.site_filter([_ip(make_ip, 1), _ip(make_ip, 2)])
    assert [r.ip for r in result] == ["10.0.0.1"]


async def test_site_filter_exception_isolated_per_record(make_ip, tester_factory):
    async def _site(rec):
        if rec.ip == "10.0.0.1":
            raise RuntimeError("boom")
        return True, 10.0

    tester = tester_factory(site_fn=_site)
    result = await tester.site_filter([_ip(make_ip, 1), _ip(make_ip, 2)])
    assert [r.ip for r in result] == ["10.0.0.2"]


async def test_revalidate_keeps_alive_only(make_ip, make_l2, tester_factory):
    """revalidate 仅返回存活项，保持原顺序。"""
    ok_ips = {"10.0.0.1", "10.0.0.3"}

    def _reval(rec):
        return rec.ip in ok_ips, 30.0

    tester = tester_factory(revalidate_fn=_reval)
    l2 = [make_l2(_ip(make_ip, 1)), make_l2(_ip(make_ip, 2)), make_l2(_ip(make_ip, 3))]
    alive = await tester.revalidate(l2)
    assert [r.ip for r in alive] == ["10.0.0.1", "10.0.0.3"]


async def test_revalidate_uses_proxy_reachability(make_ip, make_l2, tester_factory):
    """未注入 revalidate_fn 时调用真实 proxy_reachability_test（仅代理可达性）。"""
    tester = tester_factory(revalidate_fn=None)
    l2 = [make_l2(_ip(make_ip, 1)), make_l2(_ip(make_ip, 2))]
    with mock.patch(
        "app.tester.proxy_reachability_test_detailed",
        new=mock.AsyncMock(return_value=(True, 50.0, None)),
    ) as mocked:
        alive = await tester.revalidate(l2)
    assert mocked.call_count == 2
    mocked.assert_called_with("http://10.0.0.2:8002", timeout=1.0)
    assert len(alive) == 2


async def test_revalidate_exception_treated_as_dead(make_ip, make_l2, tester_factory):
    async def _reval(rec):
        raise OSError("proxy down")

    tester = tester_factory(revalidate_fn=_reval)
    l2 = [make_l2(_ip(make_ip, 1))]
    assert await tester.revalidate(l2) == []


async def test_revalidate_empty(make_ip, tester_factory):
    tester = tester_factory()
    assert await tester.revalidate([]) == []


# ---------------------------------------------------------------------------
# 批次汇总日志：total / ok / fail 与失败原因计数
# ---------------------------------------------------------------------------


async def test_site_filter_logs_summary_with_reasons(make_ip, tester_factory, caplog):
    """混合通过/超时/慢/被拒时，日志含 total/ok/fail 及各原因计数。"""

    async def _site(rec):
        if rec.ip == "10.0.0.2":
            raise TimeoutError("timeout")
        if rec.ip == "10.0.0.4":
            return False, 0.0
        lat = {"10.0.0.1": 100.0, "10.0.0.3": 3000.0}[rec.ip]
        return True, lat

    tester = tester_factory(site_fn=_site)
    recs = [_ip(make_ip, 1), _ip(make_ip, 2), _ip(make_ip, 3), _ip(make_ip, 4)]
    with caplog.at_level(logging.INFO, logger="app.tester"):
        result = await tester.site_filter(recs)
    assert [r.ip for r in result] == ["10.0.0.1"]
    text = caplog.text
    assert "site test batch: total=4 ok=1 fail=3" in text
    assert "timeout*1" in text
    assert "slow*1" in text
    assert "rejected*1" in text


async def test_site_filter_logs_timeout_count(make_ip, tester_factory, caplog):
    """10 个超时聚合为 ``timeout*10``。"""

    async def _site(rec):
        raise TimeoutError("timeout")

    tester = tester_factory(site_fn=_site)
    with caplog.at_level(logging.INFO, logger="app.tester"):
        assert await tester.site_filter([_ip(make_ip, i) for i in range(1, 11)]) == []
    assert "site test batch: total=10 ok=0 fail=10" in caplog.text
    assert "timeout*10" in caplog.text


async def test_site_filter_logs_all_ok(make_ip, tester_factory, caplog):
    """全通过时 fail=0，不输出原因计数。"""
    tester = tester_factory(site_fn=lambda rec: (True, 50.0))
    with caplog.at_level(logging.INFO, logger="app.tester"):
        await tester.site_filter([_ip(make_ip, 1), _ip(make_ip, 2)])
    assert "site test batch: total=2 ok=2 fail=0" in caplog.text


async def test_site_filter_empty_no_log(make_ip, tester_factory, caplog):
    tester = tester_factory()
    with caplog.at_level(logging.INFO, logger="app.tester"):
        assert await tester.site_filter([]) == []
    assert "site test batch" not in caplog.text


async def test_revalidate_logs_summary_with_reasons(make_ip, make_l2, tester_factory, caplog):
    """复验混合存活/超时/被拒时，日志含 total/ok/fail 及各原因计数。"""

    async def _reval(rec):
        if rec.ip == "10.0.0.2":
            raise TimeoutError("timeout")
        return rec.ip == "10.0.0.1", 10.0

    tester = tester_factory(revalidate_fn=_reval)
    l2 = [make_l2(_ip(make_ip, 1)), make_l2(_ip(make_ip, 2)), make_l2(_ip(make_ip, 3))]
    with caplog.at_level(logging.INFO, logger="app.tester"):
        alive = await tester.revalidate(l2)
    assert [r.ip for r in alive] == ["10.0.0.1"]
    text = caplog.text
    assert "revalidate batch: total=3 ok=1 fail=2" in text
    assert "timeout*1" in text
    assert "rejected*1" in text


async def test_revalidate_empty_no_log(make_ip, tester_factory, caplog):
    tester = tester_factory()
    with caplog.at_level(logging.INFO, logger="app.tester"):
        assert await tester.revalidate([]) == []
    assert "revalidate batch" not in caplog.text