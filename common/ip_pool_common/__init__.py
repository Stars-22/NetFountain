"""ip_pool_common：两级代理 IP 池系统的公共基础库。

被 level1_pool / level2_pool / proxy 三个独立项目依赖，
仅收纳稳定、通用、与业务无关的代码。
"""
from __future__ import annotations

from .api import ApiCounterMiddleware, BizCodeLogMiddleware, ErrorCode, err, ok, run_app
from .config import load_settings, load_yaml
from .logging_setup import setup_logging
from .models import (
    IpRecord,
    Level2Record,
    Protocol,
    ProviderIp,
    build_proxy_url,
)
from .testing import (
    PROBE_HOST,
    PROBE_PORT,
    PROBE_TARGET,
    batch_test,
    classify_test_error,
    proxy_reachability_test,
    proxy_reachability_test_detailed,
    site_test,
    site_test_detailed,
)

__all__ = [
    "ApiCounterMiddleware",
    "BizCodeLogMiddleware",
    "ErrorCode",
    "IpRecord",
    "Level2Record",
    "PROBE_HOST",
    "PROBE_PORT",
    "PROBE_TARGET",
    "Protocol",
    "ProviderIp",
    "batch_test",
    "build_proxy_url",
    "classify_test_error",
    "err",
    "load_settings",
    "load_yaml",
    "ok",
    "proxy_reachability_test",
    "proxy_reachability_test_detailed",
    "run_app",
    "setup_logging",
    "site_test",
    "site_test_detailed",
]

__version__ = "0.1.0"