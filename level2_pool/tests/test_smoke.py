"""冒烟测试：验证配置模块可导入，且用示例配置文件实例化成功。"""
from app.config import Level2Settings, load_level2_settings


def test_config_import_and_load():
    settings = load_level2_settings("config/level2_pool.example.yaml")
    assert isinstance(settings, Level2Settings)
    assert settings.service.port == 8001
    assert settings.site.name == "site_a"
    assert settings.sync.interval == 3.0
    assert settings.test.latency_threshold_ms == 2000
    assert settings.test.concurrency == 20
    assert settings.test.workers == 2
    assert settings.test.buffer == 20
    assert settings.revalidate_interval == 60.0
    assert settings.ttl_sweep_interval == 5.0