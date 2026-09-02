"""多开配置测试：global+pools 合并优先级、必填校验、单开兼容、旧格式兼容。

覆盖测试计划书「配置设计」扩展：全局配置与单个子池配置合并规则。
"""
from __future__ import annotations

import pytest
import yaml

from app.config import (
    Level2PoolsConfig,
    Level2Settings,
    PoolConfig,
    load_level2_pool_config,
    load_level2_pools,
    load_level2_settings,
)


def _write_yaml(tmp_path, data: dict) -> str:
    path = tmp_path / "level2_pool.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return str(path)


def test_pool_overrides_global_and_falls_back(tmp_path):
    """子池字段优先；子池未写的字段回退全局。"""
    cfg_data = {
        "global": {
            "service": {"host": "0.0.0.0", "log_level": "INFO"},
            "level1": {"base_url": "http://127.0.0.1:8000"},
            "sync": {"interval": 3.0, "timeout": 5.0},
            "test": {"latency_threshold_ms": 2000, "connect_timeout": 3.0, "concurrency": 20},
            "revalidate_interval": 60.0,
            "ttl_sweep_interval": 5.0,
        },
        "pools": [
            {"site": {"name": "site_a", "target_url": "http://www.baidu.com"},
             "service": {"port": 8001}},
            {"site": {"name": "site_b", "target_url": "https://www.example.com"},
             "service": {"port": 8002, "log_level": "DEBUG"},
             "sync": {"interval": 10.0}},
        ],
    }
    cfg = load_level2_pool_config(_write_yaml(tmp_path, cfg_data))
    assert isinstance(cfg, Level2PoolsConfig)
    assert len(cfg.pools) == 2

    a, b = cfg.pools
    assert isinstance(a, PoolConfig)
    assert a.name == "site_a"
    assert a.settings.service.port == 8001
    assert a.settings.service.log_level == "INFO"          # 全局回退
    assert a.settings.sync.interval == 3.0                  # 全局回退
    assert a.settings.test.concurrency == 20                # 全局回退
    assert a.settings.level1.base_url == "http://127.0.0.1:8000"
    assert a.settings.revalidate_interval == 60.0
    assert a.settings.ttl_sweep_interval == 5.0

    assert b.name == "site_b"
    assert b.settings.service.port == 8002
    assert b.settings.service.log_level == "DEBUG"          # 子池覆盖
    assert b.settings.sync.interval == 10.0                 # 子池覆盖
    assert b.settings.test.concurrency == 20                # 未写回退全局


def test_single_pool_means_single_open(tmp_path):
    """pools 只有一个子池 → 单开。"""
    cfg_data = {
        "global": {"service": {"port": None}},  # port 必须写在子池
        "pools": [
            {"site": {"name": "only_site", "target_url": "http://www.baidu.com"},
             "service": {"port": 8001}},
        ],
    }
    pools = load_level2_pools(_write_yaml(tmp_path, cfg_data))
    assert len(pools) == 1
    assert pools[0].settings.site.name == "only_site"


def test_missing_service_port_raises(tmp_path):
    """子池缺少必填 service.port → 明确报错。"""
    cfg_data = {
        "pools": [
            {"site": {"name": "site_a", "target_url": "http://www.baidu.com"},
             "service": {"host": "0.0.0.0"}},  # 无 port
        ],
    }
    with pytest.raises(ValueError, match="service.port"):
        load_level2_pool_config(_write_yaml(tmp_path, cfg_data))


def test_missing_site_name_raises(tmp_path):
    cfg_data = {
        "pools": [
            {"service": {"port": 8001}},  # 无 site.name
        ],
    }
    with pytest.raises(ValueError, match="site.name"):
        load_level2_pool_config(_write_yaml(tmp_path, cfg_data))


def test_duplicate_site_name_raises(tmp_path):
    cfg_data = {
        "pools": [
            {"site": {"name": "dup", "target_url": "http://a"}, "service": {"port": 8001}},
            {"site": {"name": "dup", "target_url": "http://b"}, "service": {"port": 8002}},
        ],
    }
    with pytest.raises(ValueError, match="duplicate site.name"):
        load_level2_pool_config(_write_yaml(tmp_path, cfg_data))


def test_enabled_false_soft_off(tmp_path):
    cfg_data = {
        "pools": [
            {"site": {"name": "on", "target_url": "http://a"}, "service": {"port": 8001},
             "enabled": True},
            {"site": {"name": "off", "target_url": "http://b"}, "service": {"port": 8002},
             "enabled": False},
        ],
    }
    pools = load_level2_pools(_write_yaml(tmp_path, cfg_data))
    by_name = {p.name: p for p in pools}
    assert by_name["on"].enabled is True
    assert by_name["off"].enabled is False


def test_legacy_single_site_format_compat(tmp_path):
    """旧格式（无 pools 键）→ 视为单子池，字段直接生效。"""
    data = {
        "service": {"host": "0.0.0.0", "port": 8001, "log_level": "INFO"},
        "site": {"name": "legacy_site", "target_url": "http://www.baidu.com"},
        "level1": {"base_url": "http://127.0.0.1:8000"},
        "sync": {"interval": 3.0, "timeout": 5.0},
        "test": {"latency_threshold_ms": 2000, "connect_timeout": 3.0, "concurrency": 20},
        "revalidate_interval": 60.0,
        "ttl_sweep_interval": 5.0,
    }
    path = _write_yaml(tmp_path, data)
    cfg = load_level2_pool_config(path)
    assert len(cfg.pools) == 1
    assert cfg.pools[0].settings.service.port == 8001
    assert cfg.pools[0].settings.site.name == "legacy_site"
    # load_level2_settings 旧格式行为不变
    s = load_level2_settings(path)
    assert isinstance(s, Level2Settings)
    assert s.site.name == "legacy_site"


def test_load_level2_settings_new_format_returns_first_pool(tmp_path):
    """多开格式下 load_level2_settings 返回第一个子池（保证 uvicorn app.main:app 单开兼容）。"""
    data = {
        "global": {"service": {"host": "0.0.0.0"}},
        "pools": [
            {"site": {"name": "first", "target_url": "http://a"}, "service": {"port": 8001}},
            {"site": {"name": "second", "target_url": "http://b"}, "service": {"port": 8002}},
        ],
    }
    s = load_level2_settings(_write_yaml(tmp_path, data))
    assert s.site.name == "first"
    assert s.service.port == 8001


def test_empty_pools_raises(tmp_path):
    with pytest.raises(ValueError, match="non-empty"):
        load_level2_pool_config(_write_yaml(tmp_path, {"pools": []}))


def test_reload_interval_from_global(tmp_path):
    data = {
        "global": {"reload_interval": 2.5, "service": {"host": "0.0.0.0"}},
        "pools": [
            {"site": {"name": "a", "target_url": "http://a"}, "service": {"port": 8001}},
        ],
    }
    cfg = load_level2_pool_config(_write_yaml(tmp_path, data))
    assert cfg.reload_interval == 2.5


def test_log_dir_from_global(tmp_path):
    data = {
        "global": {"log_dir": "logs", "service": {"host": "0.0.0.0"}},
        "pools": [
            {"site": {"name": "a", "target_url": "http://a"}, "service": {"port": 8001}},
        ],
    }
    cfg = load_level2_pool_config(_write_yaml(tmp_path, data))
    assert cfg.log_dir == "logs"
    # log_dir 不应泄漏进 Level2Settings（extra 禁止）
    assert cfg.pools[0].settings.service.host == "0.0.0.0"


def test_workers_string_none_coerced(tmp_path):
    """YAML 的 None/null 解析成字符串时统一回退为自动（None），不应校验失败。"""
    data = {
        "global": {"test": {"workers": "None"}},
        "pools": [
            {"site": {"name": "a", "target_url": "http://a"}, "service": {"port": 8001}},
        ],
    }
    pools = load_level2_pools(_write_yaml(tmp_path, data))
    assert pools[0].settings.test.workers is None

    data["global"]["test"]["workers"] = "null"
    pools = load_level2_pools(_write_yaml(tmp_path, data))
    assert pools[0].settings.test.workers is None

    data["global"]["test"]["workers"] = 4
    pools = load_level2_pools(_write_yaml(tmp_path, data))
    assert pools[0].settings.test.workers == 4