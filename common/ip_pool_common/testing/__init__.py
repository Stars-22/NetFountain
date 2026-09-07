"""代理测试原语：可达性测试、站点连通测试、批量并发测试。

子模块划分：
- ``errors``（见 ``ip_pool_common.errors``）：测试错误分类注册表；
- ``reachability``：纯握手代理可达性（零出口流量）；
- ``site``：站点连通（唯一出口验证）；
- ``batch``：批量并发执行器。
"""
from __future__ import annotations

from ..errors import classify_test_error, register_error_rule, reset_error_rules
from .batch import batch_test, run_batch_detailed
from .reachability import (
    PROBE_HOST,
    PROBE_PORT,
    PROBE_TARGET,
    _is_legal_proxy_reply,
    proxy_reachability_test,
    proxy_reachability_test_detailed,
)
from .site import site_test, site_test_detailed

__all__ = [
    "PROBE_HOST",
    "PROBE_PORT",
    "PROBE_TARGET",
    "batch_test",
    "classify_test_error",
    "proxy_reachability_test",
    "proxy_reachability_test_detailed",
    "register_error_rule",
    "reset_error_rules",
    "run_batch_detailed",
    "site_test",
    "site_test_detailed",
]
