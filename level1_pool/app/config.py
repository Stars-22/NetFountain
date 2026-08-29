"""一级池配置：Level1Settings（pydantic-settings）。

YAML 为基底、环境变量覆盖，字段默认值与 level1_pool/项目策划书.md「配置设计」一致。
"""
from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from ip_pool_common.config import load_settings


class ServiceConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"


class ProviderConfig(BaseModel):
    type: str = "default_http"
    api_url: str = ""
    api_key: str = ""
    trade_no: str = ""
    protocol: int = 1
    pull_count: int = 10
    pull_interval: float = 1.0
    pull_timeout: float = 5.0
    supports_ttl: bool = False


class PoolConfig(BaseModel):
    max_size: int = 500


class Level1Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEVEL1_")

    service: ServiceConfig = ServiceConfig()
    provider: ProviderConfig = ProviderConfig()
    pool: PoolConfig = PoolConfig()
    test_timeout: float = 3.0
    test_concurrency: int = 10
    test_buffer: int = 20
    test_workers: int | None = None  # None=自动 max(1, test_concurrency//pull_count)
    ttl_sweep_interval: float = 5.0


def load_level1_settings(path: str | None = None) -> Level1Settings:
    if path is None:
        return Level1Settings()
    return load_settings(Level1Settings, path, "LEVEL1_")
