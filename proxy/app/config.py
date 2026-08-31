"""代理层配置：ProxySettings（pydantic-settings）。

YAML 为基底、环境变量覆盖，字段默认值与 proxy/项目策划书.md「配置设计」一致。
"""
from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from ip_pool_common.config import load_settings


class ServiceConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 9000
    log_level: str = "INFO"


class RegistryConfig(BaseModel):
    route_file: str = "config/proxy_routes.yaml"
    route_url: str = ""
    reload_interval: float = 60.0


class Level1Config(BaseModel):
    base_url: str = "http://127.0.0.1:8000"
    timeout: float = 5.0


class DispatchConfig(BaseModel):
    timeout: float = 10.0


class ProxySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROXY_", extra="ignore")

    service: ServiceConfig = ServiceConfig()
    registry: RegistryConfig = RegistryConfig()
    level1: Level1Config = Level1Config()
    dispatch: DispatchConfig = DispatchConfig()


def load_proxy_settings(path: str | None = None) -> ProxySettings:
    if path is None:
        return ProxySettings()
    return load_settings(ProxySettings, path, "PROXY_")