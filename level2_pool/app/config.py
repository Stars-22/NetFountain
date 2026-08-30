"""二级池配置：Level2Settings（pydantic-settings）。

YAML 为基底、环境变量覆盖，字段默认值与 level2_pool/项目策划书.md「配置设计」一致。
"""
from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from ip_pool_common.config import load_settings


class ServiceConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8001
    log_level: str = "INFO"


class SiteConfig(BaseModel):
    name: str = "site_a"
    target_url: str = "http://www.baidu.com"


class Level1Config(BaseModel):
    base_url: str = "http://127.0.0.1:8000"


class SyncConfig(BaseModel):
    interval: float = 3.0
    timeout: float = 5.0


class TestConfig(BaseModel):
    latency_threshold_ms: int = 2000
    connect_timeout: float = 3.0
    concurrency: int = 20
    workers: int | None = None  # None=自动 max(1, concurrency//10)，显式值截断上限
    buffer: int = 20


class Level2Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEVEL2_")

    service: ServiceConfig = ServiceConfig()
    site: SiteConfig = SiteConfig()
    level1: Level1Config = Level1Config()
    sync: SyncConfig = SyncConfig()
    test: TestConfig = TestConfig()
    revalidate_interval: float = 60.0
    ttl_sweep_interval: float = 5.0


def load_level2_settings(path: str | None = None) -> Level2Settings:
    if path is None:
        return Level2Settings()
    return load_settings(Level2Settings, path, "LEVEL2_")
