"""日志初始化：统一格式（时间戳/服务名/级别/模块/消息），支持 stdout 与可选文件输出。

可重复调用不产生重复 handler。另支持单进程多实例场景下按线程名把日志拆分到各实例文件
（``setup_pool_logging``），并在线程注册表里记录 ``threadName → pool`` 供 ``%(pool)s`` 引用。
"""
from __future__ import annotations

import logging
import os
import threading

_DEFAULT_FMT = "%(asctime)s %(levelname)s %(service)s [%(name)s] %(message)s"
# 多开模式：额外展示子池名（pool），聚合 stdout 可据此 grep 过滤单个子池。
_POOL_FMT = "%(asctime)s %(levelname)s %(service)s %(pool)s [%(name)s] %(message)s"
_DEFAULT_DATE_FMT = "%Y-%m-%d %H:%M:%S"

_configured = False

# threadName → 子池名（子池工作线程启动时注册，用于 %(pool)s 与按线程拆分文件）
_pool_by_thread: dict[str, str] = {}
_pool_lock = threading.Lock()


def register_pool_thread(thread_name: str, pool_name: str) -> None:
    """注册 ``threadName → pool`` 映射，供日志格式化输出子池名。"""
    with _pool_lock:
        _pool_by_thread[thread_name] = pool_name


def unregister_pool_thread(thread_name: str) -> None:
    """移除线程映射；线程退出或重启动时调用。"""
    with _pool_lock:
        _pool_by_thread.pop(thread_name, None)


def pool_for_thread(thread_name: str | None) -> str | None:
    """查询某线程所属子池名；未注册返回 None。"""
    with _pool_lock:
        return _pool_by_thread.get(thread_name or "")


class _ThreadPoolFilter(logging.Filter):
    """只放行指定线程名的日志记录（用于把每个子池的日志拆到独立文件）。"""

    def __init__(self, thread_name: str) -> None:
        super().__init__()
        self._thread_name = thread_name

    def filter(self, record: logging.LogRecord) -> bool:
        return record.threadName == self._thread_name


class _ServiceFormatter(logging.Formatter):
    """在每条日志记录上注入服务名与子池名，供格式串 %(service)s / %(pool)s 引用。"""

    def __init__(self, fmt: str, service_name: str) -> None:
        super().__init__(fmt=fmt, datefmt=_DEFAULT_DATE_FMT)
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        record.service = self._service_name
        record.pool = pool_for_thread(record.threadName) or "-"
        return super().format(record)


def setup_logging(
    service_name: str,
    level: str = "INFO",
    fmt: str | None = None,
    log_file: str | None = None,
) -> None:
    """初始化结构化日志。

    统一格式：``时间戳 级别 服务名 [模块] 消息``。支持标准输出与可选文件输出；
    可重复调用：不会重复添加已有 handler，重复调用仅更新根日志级别。
    """
    global _configured

    root = logging.getLogger()
    root.setLevel(level.upper())

    fmt = fmt or _DEFAULT_FMT
    formatter = _ServiceFormatter(fmt, service_name)

    if not _configured:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)
        _configured = True

    if log_file:
        target = os.path.abspath(log_file)
        has_file = any(
            isinstance(h, logging.FileHandler)
            and os.path.abspath(getattr(h, "baseFilename", "")) == target
            for h in root.handlers
        )
        if not has_file:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)


class PoolLogHandler(logging.FileHandler):
    """子池专属日志 handler：只接收指定线程名的记录，写入对应子池日志文件。

    幂等：同一日志文件路径只添加一次 handler。
    """

    def __init__(self, log_file: str, thread_name: str, level: str = "INFO") -> None:
        super().__init__(log_file, encoding="utf-8")
        self.setLevel(level.upper())
        self.setFormatter(_ServiceFormatter(_POOL_FMT, "level2_pool"))
        self.addFilter(_ThreadPoolFilter(thread_name))


def setup_pool_logging(
    pool_name: str,
    thread_name: str,
    log_dir: str,
    level: str = "INFO",
    *,
    service_name: str = "level2_pool",
) -> str | None:
    """为单个子池挂独立文件 handler，并注册线程映射。

    - 注册 ``threadName → pool``（供聚合 stdout 的 %(pool)s 标签）；
    - 在根 logger 上添加只接收该线程名的 ``PoolLogHandler``（幂等），
      日志写入 ``log_dir/level2_pool_<pool_name>.log``；
    - 返回日志文件路径；``log_dir`` 为空时仅注册线程映射，不写文件。

    注意：多开进程内根 logger 的 stdout handler 由 ``setup_logging`` 统一初始化一次，
    本函数不重复添加 stdout handler。
    """
    register_pool_thread(thread_name, pool_name)
    if not log_dir:
        return None
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{service_name}_{pool_name}.log")
    target = os.path.abspath(log_file)
    root = logging.getLogger()
    has_pool_handler = any(
        isinstance(h, PoolLogHandler) and os.path.abspath(h.baseFilename) == target
        for h in root.handlers
    )
    if not has_pool_handler:
        handler = PoolLogHandler(log_file, thread_name, level=level)
        root.addHandler(handler)
    else:
        for h in root.handlers:
            if isinstance(h, PoolLogHandler) and os.path.abspath(h.baseFilename) == target:
                h.setLevel(level.upper())
    return log_file