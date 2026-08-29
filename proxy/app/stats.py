"""代理层运行统计：启动时间、API 被调用次数（按来源 IP / 站点）、错误计数。

供 ``/api/v1/health`` 展示代理层自身信息；不涉及任何 IP / 租赁业务数据。
计数写入由事件循环调用，但用锁保护以兼容线程模型。
"""
from __future__ import annotations

import threading
import time


class ProxyStats:
    """代理层统计：全部计数仅在进程内累计，进程重启后归零。"""

    def __init__(self, start_time: float | None = None) -> None:
        self.start_time = start_time if start_time is not None else time.time()
        self._lock = threading.Lock()
        self._total_calls = 0
        self._calls_by_ip: dict[str, int] = {}
        self._calls_by_site: dict[str, int] = {}
        self._errors: dict[int, int] = {}

    def record_call(self, ip: str | None = None) -> None:
        """记录一次代理层 API 调用（总次数 + 来源 IP）。"""
        with self._lock:
            self._total_calls += 1
            if ip:
                self._calls_by_ip[ip] = self._calls_by_ip.get(ip, 0) + 1

    def record_site(self, site: str) -> None:
        """记录一次按站点转发。"""
        with self._lock:
            self._calls_by_site[site] = self._calls_by_site.get(site, 0) + 1

    def record_error(self, error_code: int) -> None:
        """记录一次代理层错误响应（如 40400 / 50200）。"""
        with self._lock:
            self._errors[error_code] = self._errors.get(error_code, 0) + 1

    def snapshot(self) -> dict:
        """统计快照（供 health 展示，返回副本避免外部修改）。"""
        with self._lock:
            return {
                "total_calls": self._total_calls,
                "calls_by_ip": dict(self._calls_by_ip),
                "calls_by_site": dict(self._calls_by_site),
                "errors": {str(k): v for k, v in sorted(self._errors.items())},
            }