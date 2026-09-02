"""logging_setup.py 测试：冒烟、格式、级别过滤、文件输出、幂等性、子池日志拆分。"""
from __future__ import annotations

import logging
import threading

import pytest

from ip_pool_common.logging_setup import (
    _POOL_FMT,
    pool_for_thread,
    register_pool_thread,
    setup_logging,
    setup_pool_logging,
    unregister_pool_thread,
)


@pytest.fixture(autouse=True)
def _clean_logging():
    """每个测试前重置日志状态，测试后仅移除 setup_logging 添加的 handler。"""
    import ip_pool_common.logging_setup as ls

    root = logging.getLogger()

    def _ours():
        return [
            h
            for h in root.handlers
            if isinstance(getattr(h, "formatter", None), ls._ServiceFormatter)
        ]

    for handler in _ours():
        root.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()
    ls._configured = False
    ls._pool_by_thread.clear()
    yield
    for handler in _ours():
        root.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()
    ls._configured = False
    ls._pool_by_thread.clear()


def test_setup_logging_smoke(capsys):
    setup_logging("my-service", level="DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    logging.getLogger("mod.x").info("hello world")
    captured = capsys.readouterr()
    assert "my-service" in captured.err
    assert "INFO" in captured.err
    assert "mod.x" in captured.err
    assert "hello world" in captured.err


def test_setup_logging_level_filter(capsys):
    setup_logging("svc", level="WARNING")
    logging.getLogger("mod").debug("should not appear")
    logging.getLogger("mod").warning("should appear")
    captured = capsys.readouterr()
    assert "should not appear" not in captured.err
    assert "should appear" in captured.err


def test_setup_logging_file_output(tmp_path):
    log_file = tmp_path / "app.log"
    setup_logging("svc", log_file=str(log_file))
    logging.getLogger("mod.y").error("boom happened")
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = log_file.read_text(encoding="utf-8")
    assert "svc" in text
    assert "ERROR" in text
    assert "mod.y" in text
    assert "boom happened" in text


def test_setup_logging_custom_fmt(capsys):
    setup_logging("svc", fmt="CUSTOM %(levelname)s %(service)s %(message)s")
    logging.getLogger("mod").warning("warn msg")
    captured = capsys.readouterr()
    assert captured.err.startswith("CUSTOM")
    assert "warn msg" in captured.err


def test_setup_logging_no_duplicate_handlers(capsys):
    setup_logging("svc-a")
    handlers_before = list(logging.getLogger().handlers)
    setup_logging("svc-b")
    assert list(logging.getLogger().handlers) == handlers_before


def test_setup_logging_file_idempotent(tmp_path):
    log_file = tmp_path / "app.log"
    target = str(log_file)

    def _ours():
        return [
            h
            for h in logging.getLogger().handlers
            if isinstance(h, logging.FileHandler)
            and getattr(h, "baseFilename", "") == target
        ]

    setup_logging("svc", log_file=target)
    file_handlers_1 = _ours()
    setup_logging("svc", log_file=target)
    file_handlers_2 = _ours()
    assert len(file_handlers_1) == 1
    assert len(file_handlers_2) == 1


# ---------------------------------------------------------------------------
# 子池日志拆分（单进程多线程）
# ---------------------------------------------------------------------------


def test_setup_pool_logging_writes_pool_file_only(tmp_path):
    """只有对应线程名的日志写入该子池文件；其它线程不串入。"""
    log_dir = tmp_path / "logs"
    setup_pool_logging("pool_a", "level2_pool_a", str(log_dir), level="INFO")
    setup_pool_logging("pool_b", "level2_pool_b", str(log_dir), level="INFO")

    def _emit(msg: str):
        logging.getLogger("mod.x").info(msg)

    ta = threading.Thread(target=_emit, args=("from pool a",), name="level2_pool_a")
    tb = threading.Thread(target=_emit, args=("from pool b",), name="level2_pool_b")
    ta.start()
    tb.start()
    ta.join()
    tb.join()

    for handler in logging.getLogger().handlers:
        handler.flush()

    log_a = (log_dir / "level2_pool_pool_a.log").read_text(encoding="utf-8")
    log_b = (log_dir / "level2_pool_pool_b.log").read_text(encoding="utf-8")
    assert "from pool a" in log_a
    assert "from pool b" not in log_a
    assert "from pool b" in log_b
    assert "from pool a" not in log_b


def test_setup_pool_logging_idempotent(tmp_path):
    log_dir = tmp_path / "logs"
    setup_pool_logging("pool_a", "level2_pool_a", str(log_dir), level="INFO")
    setup_pool_logging("pool_a", "level2_pool_a", str(log_dir), level="DEBUG")
    pool_handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler) and "pool_a" in h.baseFilename
    ]
    assert len(pool_handlers) == 1, "同一子池文件不应重复添加 handler"
    assert pool_handlers[0].level == logging.DEBUG, "重复调用应更新级别"


def test_pool_for_thread_registration():
    register_pool_thread("level2_pool_a", "pool_a")
    assert pool_for_thread("level2_pool_a") == "pool_a"
    assert pool_for_thread("unknown") is None
    unregister_pool_thread("level2_pool_a")
    assert pool_for_thread("level2_pool_a") is None


def test_pool_fmt_injects_pool_label(capsys):
    """聚合 stdout 使用 _POOL_FMT 时，每条日志带上子池名，未注册线程显示 '-'。"""
    setup_logging("level2_pool", fmt=_POOL_FMT)
    register_pool_thread("level2_pool_a", "pool_a")

    def _emit(msg: str):
        logging.getLogger("app.syncer").info(msg)

    t = threading.Thread(target=_emit, args=("aggregate line",), name="level2_pool_a")
    t.start()
    t.join()
    captured = capsys.readouterr()
    assert "pool_a" in captured.err
    assert "aggregate line" in captured.err