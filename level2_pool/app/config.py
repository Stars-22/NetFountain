"""二级池配置：Level2Settings（pydantic-settings）+ 多子池装配（global + pools）。

- ``Level2Settings`` 单实例配置：YAML 为基底、环境变量覆盖；
- 多开格式：``config/level2_pool.yaml`` 顶层分 ``global``（全局默认）与 ``pools``（子池列表），
  每个子池配置深合并全局配置，子池字段优先；仅一个子池时单开；
- ``load_level2_pool_config`` 返回装配结果，``load_level2_settings`` 保持单实例兼容。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ip_pool_common.config import load_settings, load_yaml


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

    @field_validator("workers", mode="before")
    @classmethod
    def _coerce_workers_none(cls, v):
        # YAML 的 None/null 可能被解析成字符串 "None"/"null"，统一转回 None（自动）
        if isinstance(v, str) and v.strip().lower() in ("none", "null", ""):
            return None
        return v


class Level2Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEVEL2_")

    service: ServiceConfig = ServiceConfig()
    site: SiteConfig = SiteConfig()
    level1: Level1Config = Level1Config()
    sync: SyncConfig = SyncConfig()
    test: TestConfig = TestConfig()
    revalidate_interval: float = 60.0
    ttl_sweep_interval: float = 5.0


@dataclass
class PoolConfig:
    """单个子池的最终配置（已与全局配置合并）。"""

    name: str                       # 子池名（= settings.site.name，唯一键）
    settings: Level2Settings
    enabled: bool = True            # false = 软关闭（配置里仍保留，但不启动）

    @property
    def port(self) -> int:
        return self.settings.service.port


@dataclass
class Level2PoolsConfig:
    """多开装配结果：全局默认 + 子池列表 + 全局可调项。"""

    pools: list[PoolConfig] = field(default_factory=list)
    reload_interval: float = 5.0    # 配置文件热检查周期（秒）
    log_dir: str | None = None      # 子池日志文件目录（可选）


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并两个 dict：嵌套 dict 逐层合并，标量键值覆盖。"""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_level2_pool_config(path: str) -> Level2PoolsConfig:
    """读取多开配置文件并装配全部子池。

    顶层结构：
    - ``global``：全局默认（可选，缺省用 Level2Settings 默认值）；
    - ``pools``：子池列表（必填非空），每项深合并全局；子池未写字段回退全局；
    - ``global.reload_interval``：配置热检查周期。

    兼容旧格式：文件不含 ``pools`` 键时视为单站点旧配置，返回单个子池（单开）。
    """
    data = load_yaml(path)
    if "pools" not in data:
        settings = Level2Settings(**data)
        return Level2PoolsConfig(pools=[PoolConfig(name=settings.site.name, settings=settings)])

    global_data = data.get("global") or {}
    if not isinstance(global_data, dict):
        raise ValueError("config 'global' must be a mapping")

    # reload_interval / log_dir 是启动器级配置，不属于单实例 Level2Settings，取出后不再合并进子池
    reload_interval = global_data.get("reload_interval", 5.0)
    log_dir = global_data.get("log_dir")
    pool_base = {k: v for k, v in global_data.items() if k not in ("reload_interval", "log_dir")}

    raw_pools = data["pools"]
    if not isinstance(raw_pools, list) or not raw_pools:
        raise ValueError("config 'pools' must be a non-empty list")

    pools: list[PoolConfig] = []
    seen: set[str] = set()
    for idx, raw in enumerate(raw_pools):
        if not isinstance(raw, dict):
            raise ValueError(f"pools[{idx}] must be a mapping")
        enabled = raw.pop("enabled", True)
        merged = _deep_merge(pool_base, raw)
        site_name = merged.get("site", {}).get("name")
        if not site_name:
            raise ValueError(f"pools[{idx}] missing required site.name")
        if site_name in seen:
            raise ValueError(f"duplicate site.name in pools: {site_name!r}")
        service = merged.get("service") or {}
        if not isinstance(service, dict) or "port" not in service:
            raise ValueError(f"pools[{idx}] (site.name={site_name}) missing required service.port")
        settings = Level2Settings(**merged)
        seen.add(site_name)
        pools.append(PoolConfig(name=site_name, settings=settings, enabled=bool(enabled)))

    reload_interval = global_data.get("reload_interval", 5.0)
    return Level2PoolsConfig(
        pools=pools,
        reload_interval=float(reload_interval),
        log_dir=log_dir,
    )


def load_level2_pools(path: str) -> list[PoolConfig]:
    """便捷：返回 ``load_level2_pool_config(path)`` 的子池列表。"""
    return load_level2_pool_config(path).pools


def load_level2_settings(path: str | None = None) -> Level2Settings:
    """加载单实例配置。

    - 多开格式（含 ``pools`` 键）时返回第一个子池的合并配置，保证 ``uvicorn app.main:app`` 单开兼容；
    - 旧格式（无 ``pools`` 键）按原逻辑加载。
    """
    if path is None:
        return Level2Settings()
    data = load_yaml(path)
    if "pools" in data:
        return load_level2_pool_config(path).pools[0].settings
    return load_settings(Level2Settings, path, "LEVEL2_")
