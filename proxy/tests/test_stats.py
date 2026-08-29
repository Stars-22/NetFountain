"""stats.py 单元测试：ProxyStats 计数与快照。"""
from __future__ import annotations

from app.stats import ProxyStats


def test_snapshot_empty():
    s = ProxyStats(start_time=1000.0)
    assert s.start_time == 1000.0
    assert s.snapshot() == {
        "total_calls": 0,
        "calls_by_ip": {},
        "calls_by_site": {},
        "errors": {},
    }


def test_record_counts():
    s = ProxyStats(start_time=1000.0)
    s.record_call(ip="1.2.3.4")
    s.record_site("site_a")
    s.record_call(ip="1.2.3.4")
    s.record_site("site_a")
    s.record_call(ip="5.6.7.8")
    s.record_site("site_b")
    s.record_call(ip="9.9.9.9")
    s.record_error(40400)
    snap = s.snapshot()
    assert snap["total_calls"] == 4
    assert snap["calls_by_ip"] == {"1.2.3.4": 2, "5.6.7.8": 1, "9.9.9.9": 1}
    assert snap["calls_by_site"] == {"site_a": 2, "site_b": 1}
    assert snap["errors"] == {"40400": 1}


def test_snapshot_returns_copy():
    s = ProxyStats(start_time=1000.0)
    s.record_call(ip="1.2.3.4")
    snap = s.snapshot()
    snap["calls_by_ip"]["hacked"] = 99
    assert s.snapshot()["calls_by_ip"] == {"1.2.3.4": 1}