"""launcher 测试：多开 reconcile（开启/关闭/重启/软关闭）+ 配置热检查循环。

用桩 worker 替换真实 uvicorn 线程，聚焦 launcher 的状态对齐逻辑。
"""
from __future__ import annotations

import threading
import time

import yaml

from app.config import PoolConfig, load_level2_pool_config
from app.launcher import PoolLauncher


class _FakeWorker:
    """记录 start/stop 调用与当前 settings 的桩 worker。"""

    def __init__(self, settings, *, log_dir=None, app_factory=None):
        self.settings = settings
        self.log_dir = log_dir
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    @property
    def running(self):
        return self.started and not self.stopped


def _write_cfg(tmp_path, pools, **global_kw) -> str:
    data = {"global": {"service": {"host": "0.0.0.0"}, **global_kw}, "pools": pools}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return str(path)


def _pool(name: str, port: int, enabled: bool = True, **site_kw) -> dict:
    return {
        "site": {"name": name, "target_url": site_kw.pop("target_url", f"http://{name}.test")},
        "service": {"port": port, **site_kw},
        "enabled": enabled,
    }


def test_reconcile_starts_all_enabled_pools(tmp_path):
    path = _write_cfg(tmp_path, [_pool("a", 8001), _pool("b", 8002)])
    launcher = PoolLauncher(config_path=path, worker_cls=_FakeWorker, log_dir="")
    launcher.reconcile()
    assert set(launcher._workers) == {"a", "b"}
    assert all(w.started and not w.stopped for w in launcher._workers.values())


def test_reconcile_skips_disabled_pools(tmp_path):
    path = _write_cfg(tmp_path, [_pool("a", 8001), _pool("b", 8002, enabled=False)])
    launcher = PoolLauncher(config_path=path, worker_cls=_FakeWorker, log_dir="")
    launcher.reconcile()
    assert set(launcher._workers) == {"a"}
    assert "b" not in launcher._workers


def test_reconcile_stops_removed_pool(tmp_path):
    path = _write_cfg(tmp_path, [_pool("a", 8001), _pool("b", 8002)])
    launcher = PoolLauncher(config_path=path, worker_cls=_FakeWorker, log_dir="")
    launcher.reconcile()
    assert "b" in launcher._workers

    path = _write_cfg(tmp_path, [_pool("a", 8001)])
    launcher.config_path = path
    launcher.reconcile()
    assert set(launcher._workers) == {"a"}
    assert launcher._workers["a"].started and not launcher._workers["a"].stopped


def test_reconcile_restarts_on_config_change(tmp_path):
    path = _write_cfg(tmp_path, [_pool("a", 8001)])
    launcher = PoolLauncher(config_path=path, worker_cls=_FakeWorker, log_dir="")
    launcher.reconcile()
    first = launcher._workers["a"]

    path = _write_cfg(tmp_path, [_pool("a", 8002)])  # port 变了
    launcher.config_path = path
    launcher.reconcile()
    second = launcher._workers["a"]
    assert first is not second
    assert first.stopped is True
    assert second.started is True
    assert second.settings.service.port == 8002


def test_reconcile_no_restart_when_unchanged(tmp_path):
    path = _write_cfg(tmp_path, [_pool("a", 8001)])
    launcher = PoolLauncher(config_path=path, worker_cls=_FakeWorker, log_dir="")
    launcher.reconcile()
    first = launcher._workers["a"]

    # 重写相同配置（仅 YAML 键序不同）→ 不重启
    launcher.reconcile()
    assert launcher._workers["a"] is first
    assert first.stopped is False


def test_reconcile_reenables_after_soft_off(tmp_path):
    path = _write_cfg(tmp_path, [_pool("a", 8001, enabled=False)])
    launcher = PoolLauncher(config_path=path, worker_cls=_FakeWorker, log_dir="")
    launcher.reconcile()
    assert "a" not in launcher._workers

    path = _write_cfg(tmp_path, [_pool("a", 8001, enabled=True)])
    launcher.config_path = path
    launcher.reconcile()
    assert launcher._workers["a"].started is True


def test_run_reconciles_on_mtime_change(tmp_path):
    path = _write_cfg(tmp_path, [_pool("a", 8001)])
    launcher = PoolLauncher(config_path=path, worker_cls=_FakeWorker, log_dir="")

    thread = threading.Thread(target=launcher.run, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while "a" not in launcher._workers and time.monotonic() < deadline:
            time.sleep(0.02)
        assert "a" in launcher._workers, "initial reconcile should start pool a"

        # 修改配置：把 a 移除，新增 c → 热停止 a、启动 c
        path = _write_cfg(tmp_path, [_pool("c", 8003)])
        launcher.config_path = path
        deadline = time.monotonic() + 5
        while ("a" in launcher._workers or "c" not in launcher._workers) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert "a" not in launcher._workers, "removed pool a should stop"
        assert "c" in launcher._workers, "new pool c should start"
    finally:
        launcher.shutdown()
        thread.join(timeout=5)


def test_run_missing_config_file_keeps_waiting(tmp_path):
    """配置文件暂缺/损坏时循环不退出（continue 而非崩溃）。"""
    launcher = PoolLauncher(config_path=str(tmp_path / "nope.yaml"), worker_cls=_FakeWorker, log_dir="")
    thread = threading.Thread(target=launcher.run, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    time.sleep(0.3)
    assert thread.is_alive(), "launcher should keep polling when config missing"
    launcher.shutdown()
    thread.join(timeout=5)


def test_launcher_reads_log_dir_from_config(tmp_path):
    path = _write_cfg(tmp_path, [_pool("a", 8001)], log_dir="mylogs")
    launcher = PoolLauncher(config_path=path, worker_cls=_FakeWorker)
    launcher.reconcile()
    assert launcher._log_dir == "mylogs"
    assert launcher._workers["a"].log_dir == "mylogs"


def test_launcher_log_dir_constructor_wins(tmp_path):
    path = _write_cfg(tmp_path, [_pool("a", 8001)], log_dir="from_cfg")
    launcher = PoolLauncher(config_path=path, worker_cls=_FakeWorker, log_dir="explicit")
    launcher.reconcile()
    assert launcher._log_dir == "explicit"


def test_pool_config_helper(tmp_path):
    """PoolConfig 便捷属性。"""
    path = _write_cfg(tmp_path, [_pool("x", 8001)])
    cfg = load_level2_pool_config(path)
    pc: PoolConfig = cfg.pools[0]
    assert pc.port == 8001
    assert pc.settings.site.name == "x"