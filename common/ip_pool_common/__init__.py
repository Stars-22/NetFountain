"""ip_pool_common：两级代理 IP 池系统的公共基础库。

被 level1_pool / level2_pool / proxy 三个独立项目依赖，
仅收纳稳定、通用、与业务无关的代码。

模块划分：
- ``models``      纯数据结构（协议枚举、各级记录）
- ``testing``     代理测试原语（可达性/站点连通/批量执行，``testing.errors`` 分类注册表）
- ``config``      YAML + 环境变量配置加载、deep_merge
- ``logging_setup`` 日志初始化与子池日志拆分
- ``api``         统一错误码/响应封装/ASGI 中间件/启动入口
- ``concurrency`` 周期任务、tick 循环、有界队列批处理管线
"""
from __future__ import annotations

from .api import ApiCounterMiddleware, BizCodeLogMiddleware, ErrorCode, err, ok, run_app
from .concurrency import (
    BoundedTestPipeline,
    TickLoop,
    resolve_worker_count,
    run_periodic,
)
from .config import deep_merge, load_settings, load_yaml
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
    "BoundedTestPipeline",
    "ErrorCode",
    "IpRecord",
    "Level2Record",
    "PROBE_HOST",
    "PROBE_PORT",
    "PROBE_TARGET",
    "Protocol",
    "ProviderIp",
    "TickLoop",
    "batch_test",
    "build_proxy_url",
    "classify_test_error",
    "deep_merge",
    "err",
    "load_settings",
    "load_yaml",
    "ok",
    "proxy_reachability_test",
    "proxy_reachability_test_detailed",
    "resolve_worker_count",
    "run_app",
    "run_periodic",
    "setup_logging",
    "site_test",
    "site_test_detailed",
]

__version__ = "0.2.0"
