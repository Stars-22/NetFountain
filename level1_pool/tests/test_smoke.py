"""冒烟测试：验证配置模块可导入，且示例配置（新/旧格式）实例化成功。"""
from app.config import Level1Settings, load_level1_pool_config, load_level1_settings


def test_config_import_and_load():
    settings = load_level1_settings("config/level1_pool.example.yaml")
    assert isinstance(settings, Level1Settings)
    assert settings.service.port == 8000
    assert settings.pool.max_size == 500
    assert settings.ttl_sweep_interval == 5.0
    assert settings.providers is not None
    assert [p.name for p in settings.providers] == [
        "http91_main", "freeproxy_main", "backup_http",
    ]
    # provider / 顶层 test_* 兼容字段 = 第一个供应商合并结果
    assert settings.provider.type == "http91"
    assert settings.provider.pull_count == 10
    assert settings.test_concurrency == 50
    # 第二个供应商（freeproxy）：专属参数生效，测试管线独立配置
    p2 = settings.providers[1]
    assert p2.type == "freeproxy"
    assert p2.api_url == "http://www.zdopen.com/FreeProxy/Get/"
    assert p2.trade_no == "<你的app_id>" and p2.api_key == "<你的akey>"
    assert p2.dalu == 1 and p2.protocol_type == 0
    assert p2.pull_count == 100 and p2.pull_interval == 5.0
    assert p2.supports_ttl is False and p2.enabled is True
    # 第三个供应商（default_http）：拉取参数回退 global，独立 test_* 亦回退 global，软关闭
    p3 = settings.providers[2]
    assert p3.enabled is False
    assert p3.pull_count == 10 and p3.pull_interval == 1.0
    assert p3.test_timeout == 3.0
    assert p3.test_concurrency == 10 and p3.test_buffer == 20 and p3.test_workers == 5


def test_multi_config_loader():
    cfg = load_level1_pool_config("config/level1_pool.example.yaml")
    assert cfg.service.port == 8000
    assert cfg.pool.max_size == 500
    assert cfg.ttl_sweep_interval == 5.0
    assert [p.name for p in cfg.providers] == [
        "http91_main", "freeproxy_main", "backup_http",
    ]
    first = cfg.providers[0]
    assert first.type == "http91"
    assert first.api_url == "http://api.91http.com/v1/get-ip"
    assert (first.test_timeout, first.test_concurrency, first.test_buffer, first.test_workers) == (
        3.0, 50, 100, 5,
    )
