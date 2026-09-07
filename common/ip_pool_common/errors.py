"""测试错误分类注册表：将测试异常归类为简短原因键。

判定采用有序规则表（先注册先匹配），内置规则保持既有语义与顺序：

1. timeout（``asyncio.TimeoutError``/``TimeoutError``）
2. connect（``OSError``/``ConnectionError``）
3. proxy_reject（代理协议拒绝）
4. invalid_proxy（``ValueError``）
5. client_error（``aiohttp.ClientError``）
6. exception（兜底）

开闭原则：新增异常类型/原因键只需 ``register_error_rule`` 注册，
无需修改 ``classify_test_error`` 本体。
"""
from __future__ import annotations

import asyncio
import threading

import aiohttp
from aiohttp_socks import ProxyError
from python_socks import ProxyError as PySocksProxyError
from python_socks._protocols.errors import ReplyError

__all__ = [
    "classify_test_error",
    "register_error_rule",
    "reset_error_rules",
]


def _default_rules() -> list[tuple[tuple[type[BaseException], ...], str]]:
    """内置规则（顺序敏感：先超时、再连接、后代理协议/业务类异常）。

    依赖异常继承层级（已验证）：
    - ``ProxyTimeoutError`` 是 ``TimeoutError``/``OSError`` 子类；
    - ``ProxyConnectionError`` 是 ``OSError`` 子类；
    - ``aiohttp.ClientConnectorError`` 是 ``OSError`` 子类；
    - ``aiohttp.ServerTimeoutError`` 是 ``TimeoutError`` 子类。
    """
    return [
        ((asyncio.TimeoutError, TimeoutError), "timeout"),
        ((OSError, ConnectionError), "connect"),
        ((ProxyError, PySocksProxyError, ReplyError), "proxy_reject"),
        ((ValueError,), "invalid_proxy"),
        ((aiohttp.ClientError,), "client_error"),
    ]


_rules: list[tuple[tuple[type[BaseException], ...], str]] = _default_rules()
_rules_lock = threading.Lock()

#: 所有规则都不匹配时的兜底原因键
FALLBACK_REASON = "exception"


def register_error_rule(
    exc_types: tuple[type[BaseException], ...],
    reason: str,
    *,
    position: int | None = None,
) -> None:
    """注册分类规则：``exc_types`` 中的异常归类为 ``reason``。

    默认插入到兜底之前（即优先级最低的内置规则之后）；
    ``position`` 可显式指定插入位置（越早越优先）。
    """
    with _rules_lock:
        if position is None:
            position = len(_rules)
        _rules.insert(min(position, len(_rules)), (exc_types, reason))


def reset_error_rules() -> None:
    """恢复内置规则（供测试隔离使用）。"""
    global _rules
    with _rules_lock:
        _rules = _default_rules()


def classify_test_error(error: BaseException) -> str:
    """将测试异常归类为简短原因键，供批次汇总日志展示。

    按注册顺序判定，全部不匹配返回 ``FALLBACK_REASON``。
    """
    with _rules_lock:
        rules = list(_rules)
    for exc_types, reason in rules:
        if isinstance(error, exc_types):
            return reason
    return FALLBACK_REASON
