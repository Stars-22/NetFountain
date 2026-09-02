"""多开启动器：单进程多线程运行多个二级池子池实例。

- 读取 ``config/level2_pool.yaml``（``global`` + ``pools`` 格式）；
- 每个子池一个 ``PoolWorker`` 线程，独立事件循环运行 uvicorn server；
- 后台循环轮询配置文件 mtime，变化即 reconcile：新增→开启、删除/``enabled:false``→关闭、
  配置改动→重启；
- 每个子池线程命名 ``level2_<site>``，日志按线程拆到 ``log_dir/level2_pool_<site>.log``。

用法：``python -m app.launcher``（默认读 ``config/level2_pool.yaml``，可用 ``--config`` 覆盖）。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import threading
import time

import uvicorn

from ip_pool_common.logging_setup import setup_logging, setup_pool_logging

from .config import Level2PoolsConfig, Level2Settings, load_level2_pool_config
from .main import create_app

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config", "level2_pool.yaml"
)


class PoolWorker:
    """单个子池实例：独立线程 + 独立事件循环运行 uvicorn server。"""

    def __init__(
        self,
        settings: Level2Settings,
        *,
        log_dir: str | None = None,
        app_factory=create_app,
    ) -> None:
        self.settings = settings
        self.log_dir = log_dir
        self._app_factory = app_factory
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._stop_requested = threading.Event()

    def start(self) -> None:
        name = f"level2_{self.settings.site.name}"
        self._thread = threading.Thread(
            target=self._run, name=name, daemon=True
        )
        self._thread.start()
        logger.info("pool %s started on port %s", self.settings.site.name, self.settings.service.port)

    def _run(self) -> None:
        thread_name = threading.current_thread().name
        try:
            setup_pool_logging(
                self.settings.site.name,
                thread_name,
                self.log_dir or "",
                level=self.settings.service.log_level,
            )
            app = self._app_factory(self.settings, configure_logging=False)
            config = uvicorn.Config(
                app,
                host=self.settings.service.host,
                port=self.settings.service.port,
                log_level=self.settings.service.log_level.lower(),
            )
            self._server = uvicorn.Server(config)
            asyncio.run(self._server.serve())
        except Exception:  # noqa: BLE001
            logger.exception("pool %s thread crashed", self.settings.site.name)
        finally:
            from ip_pool_common.logging_setup import unregister_pool_thread

            unregister_pool_thread(thread_name)

    def stop(self, timeout: float = 10.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._server = None
        self._thread = None
        logger.info("pool %s stopped", self.settings.site.name)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


class PoolLauncher:
    """多开管理器：根据配置文件启动/关闭/重启子池线程。"""

    def __init__(
        self,
        config_path: str | None = None,
        *,
        worker_cls=PoolWorker,
        log_dir: str | None = None,
    ) -> None:
        self.config_path = config_path or _CONFIG_PATH
        self._worker_cls = worker_cls
        self._log_dir = log_dir
        self._workers: dict[str, object] = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_mtime: float | None = None

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------

    def _load_config(self) -> Level2PoolsConfig:
        cfg = load_level2_pool_config(self.config_path)
        # 优先级：显式构造 log_dir > 配置文件 global.log_dir > 不启用文件日志
        if self._log_dir is None:
            self._log_dir = cfg.log_dir or ""
        return cfg

    # ------------------------------------------------------------------
    # reconcile：把当前运行状态对齐到配置文件
    # ------------------------------------------------------------------

    def reconcile(self) -> None:
        cfg = self._load_config()
        desired = {p.name: p for p in cfg.pools}
        with self._lock:
            # 关闭：已被删除 / enabled=false
            for name in list(self._workers):
                pool_cfg = desired.get(name)
                if pool_cfg is None or not pool_cfg.enabled:
                    self._stop_worker(name)
            # 重启：配置有变化
            for name, pool_cfg in desired.items():
                worker = self._workers.get(name)
                if worker is None or not pool_cfg.enabled:
                    continue
                if _settings_of(worker) != pool_cfg.settings:
                    self._stop_worker(name)
                    self._start_worker(pool_cfg)
            # 启动：新增 / 重新启用
            for name, pool_cfg in desired.items():
                if pool_cfg.enabled and name not in self._workers:
                    self._start_worker(pool_cfg)

    def _start_worker(self, pool_cfg) -> None:
        worker = self._worker_cls(pool_cfg.settings, log_dir=self._log_dir)
        worker.start()
        self._workers[pool_cfg.name] = worker

    def _stop_worker(self, name: str) -> None:
        worker = self._workers.pop(name, None)
        if worker is not None:
            worker.stop()

    # ------------------------------------------------------------------
    # 运行循环：轮询配置 mtime，变化即 reconcile
    # ------------------------------------------------------------------

    def run(self, poll_interval: float = 5.0) -> None:
        setup_logging("level2_pool")
        logger.info("launcher reading config %s", self.config_path)
        try:
            self.reconcile()
            self._last_mtime = self._file_mtime()
        except OSError:
            logger.warning("config file not readable yet: %s", self.config_path)
        while not self._stop.is_set():
            time.sleep(poll_interval)
            try:
                mtime = self._file_mtime()
            except OSError:
                continue
            if mtime != self._last_mtime:
                logger.info("config file changed, reconciling")
                try:
                    self.reconcile()
                    self._last_mtime = mtime
                except Exception:  # noqa: BLE001
                    logger.exception("reconcile failed, keep current workers")

    def _file_mtime(self) -> float:
        return os.path.getmtime(self.config_path)

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            for name in list(self._workers):
                self._stop_worker(name)


def _settings_of(worker: object) -> Level2Settings:
    return worker.settings  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Level2 pool multi-open launcher")
    parser.add_argument("--config", default=None, help="config yaml path (default: config/level2_pool.yaml)")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="config check interval seconds")
    args = parser.parse_args(argv)

    launcher = PoolLauncher(config_path=args.config)
    try:
        launcher.run(poll_interval=args.poll_interval)
    except KeyboardInterrupt:
        logger.info("received interrupt, shutting down")
    finally:
        launcher.shutdown()


if __name__ == "__main__":
    main()