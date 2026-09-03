"""一级池配置：Level1Settings（pydantic-settings）+ 多供应商装配（global + providers）。

- ``Level1Settings`` 单实例配置：YAML 为基底、环境变量覆盖，字段默认值与
  level1_pool/项目策划书.md「配置设计」一致；
- 多供应商格式：``config/level1_pool.yaml`` 顶层 ``global``（供应商拉取/测试默认值，
  可选）+ ``providers``（供应商列表），每个供应商条目深合并 global、条目字段优先；
  每个供应商条目内可独立配置 ``test_timeout`` / ``test_concurrency`` / ``test_buffer``
  / ``test_workers``，独立拉取器（PullTask）+ 独立测试器（Tester），共享同一池；
  ``service`` / ``pool`` / ``ttl_sweep_interval`` 仍为顶层共享配置；
- ``load_level1_pool_config`` 返回装配结果；``load_level1_settings`` 保持
  Level1Settings 单实例兼容（新格式时 ``providers`` 带全部供应商，
  ``provider`` / 顶层 ``test_*`` 取第一个供应商的合并结果）；
- 兼容旧格式：文件不含 ``providers`` 键时视为单供应商旧配置（顶层 ``provider:``
  块 + 顶层 ``test_*`` 字段）。
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
    port: int = 8000
    log_level: str = "INFO"


class ProviderConfig(BaseModel):
    type: str = "default_http"
    api_url: str = ""
    api_key: str = ""
    trade_no: str = ""
    protocol: int = 1
    dalu: int = 1  # freeproxy 专用：区域选择，1=大陆，0=海外（必选参数）
    protocol_type: int = 0  # freeproxy 专用：0=不发送(全部)，1=http，2=socks4，3=socks5，4=https
    pull_count: int = 10
    pull_interval: float = 1.0
    pull_timeout: float = 5.0
    supports_ttl: bool = False
    name: str = ""  # 供应商唯一名称（/status 明细键，缺省自动 provider_N）
    enabled: bool = True  # false = 软关闭（配置保留但不启动拉取器）


class ProviderRuntime(ProviderConfig):
    """单个供应商的最终运行配置（拉取 + 独立测试管线参数）。"""

    test_timeout: float = 3.0
    test_concurrency: int = 10
    test_buffer: int = 20
    test_workers: int | None = None  # None=自动 max(1, test_concurrency//pull_count)

    @field_validator("test_workers", mode="before")
    @classmethod
    def _coerce_workers_none(cls, v):
        # YAML 的 None/null 可能被解析成字符串 "None"/"null"，统一转回 None（自动）
        if isinstance(v, str) and v.strip().lower() in ("none", "null", ""):
            return None
        return v


class PoolConfig(BaseModel):
    max_size: int = 500


class Level1Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEVEL1_")

    service: ServiceConfig = ServiceConfig()
    provider: ProviderConfig = ProviderConfig()
    # 新格式多供应商（global+providers 装配结果）；None=旧格式单 provider
    providers: list[ProviderRuntime] | None = None
    pool: PoolConfig = PoolConfig()
    test_timeout: float = 3.0
    test_concurrency: int = 10
    test_buffer: int = 20
    test_workers: int | None = None  # None=自动 max(1, test_concurrency//pull_count)
    ttl_sweep_interval: float = 5.0


@dataclass
class Level1MultiConfig:
    """多供应商装配结果：合并后的供应商列表 + 共享配置。"""

    providers: list[ProviderRuntime] = field(default_factory=list)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    pool: PoolConfig = field(default_factory=PoolConfig)
    ttl_sweep_interval: float = 5.0


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并两个 dict：嵌套 dict 逐层合并，标量键值覆盖。"""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_level1_pool_config(path: str) -> Level1MultiConfig:
    """读取配置文件并装配全部供应商。

    顶层结构（新格式）：
    - ``global``：供应商条目默认值（可选，缺省用代码默认值）；
    - ``providers``：供应商列表（必填非空），每项深合并 global；条目未写字段回退
      global，global 未写回退代码默认值；
    - ``service`` / ``pool`` / ``ttl_sweep_interval``：共享配置（不参与供应商合并）。

    兼容旧格式：文件不含 ``providers`` 键时视为单供应商旧配置（顶层 ``provider:``
    块 + 顶层 ``test_*`` 字段），返回单个供应商。
    """
    data = load_yaml(path)
    if "providers" not in data:
        settings = load_settings(Level1Settings, path, "LEVEL1_")
        runtime = ProviderRuntime(
            **settings.provider.model_dump(),
            test_timeout=settings.test_timeout,
            test_concurrency=settings.test_concurrency,
            test_buffer=settings.test_buffer,
            test_workers=settings.test_workers,
        )
        if not runtime.name:
            runtime.name = "provider_1"
        return Level1MultiConfig(
            providers=[runtime],
            service=settings.service,
            pool=settings.pool,
            ttl_sweep_interval=settings.ttl_sweep_interval,
        )

    global_data = data.get("global") or {}
    if not isinstance(global_data, dict):
        raise ValueError("config 'global' must be a mapping")

    raw_providers = data["providers"]
    if not isinstance(raw_providers, list) or not raw_providers:
        raise ValueError("config 'providers' must be a non-empty list")

    providers: list[ProviderRuntime] = []
    seen: set[str] = set()
    for idx, raw in enumerate(raw_providers):
        if not isinstance(raw, dict):
            raise ValueError(f"providers[{idx}] must be a mapping")
        merged = _deep_merge(global_data, raw)
        name = str(merged.get("name") or "").strip() or f"provider_{idx + 1}"
        if name in seen:
            raise ValueError(f"duplicate provider name in providers: {name!r}")
        if not str(merged.get("type") or "").strip():
            raise ValueError(f"providers[{idx}] (name={name}) missing required 'type'")
        runtime = ProviderRuntime(**merged)
        runtime.name = name
        seen.add(name)
        providers.append(runtime)

    service_data = data.get("service") or {}
    pool_data = data.get("pool") or {}
    if not isinstance(service_data, dict) or not isinstance(pool_data, dict):
        raise ValueError("config 'service'/'pool' must be mappings")
    return Level1MultiConfig(
        providers=providers,
        service=ServiceConfig(**service_data),
        pool=PoolConfig(**pool_data),
        ttl_sweep_interval=float(data.get("ttl_sweep_interval", 5.0)),
    )


def load_level1_settings(path: str | None = None) -> Level1Settings:
    """加载单实例配置。

    - 多供应商格式（含 ``providers`` 键）时返回第一个供应商的合并结果（单开兼容），
      ``providers`` 携带全部供应商；
    - 旧格式（无 ``providers`` 键）按原逻辑加载（YAML 基底 + 环境变量覆盖）。
    """
    if path is None:
        return Level1Settings()
    data = load_yaml(path)
    if "providers" not in data:
        return load_settings(Level1Settings, path, "LEVEL1_")
    multi = load_level1_pool_config(path)
    first = multi.providers[0]
    return Level1Settings(
        service=multi.service,
        pool=multi.pool,
        ttl_sweep_interval=multi.ttl_sweep_interval,
        provider=ProviderConfig(**{k: getattr(first, k) for k in ProviderConfig.model_fields}),
        providers=multi.providers,
        test_timeout=first.test_timeout,
        test_concurrency=first.test_concurrency,
        test_buffer=first.test_buffer,
        test_workers=first.test_workers,
    )
