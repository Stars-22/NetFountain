"""供应商客户端层。

- ``base``：BaseProvider 抽象（模板方法 ``pull``）+ ProviderFactory 注册工厂
  + 共享解析工具；
- ``default_http`` / ``http91`` / ``freeproxy`` / ``juliangip``（不限量 + 动态包时/包量）：
  具体供应商实现，各自独立文件；
- ``__init__``：导入全部具体供应商完成注册副作用，并作为统一导出口。

扩展新供应商：新建文件继承 BaseProvider、实现 ``_params`` 与
``_parse``、``@register("名字")`` 装饰，并在本包 ``__init__`` 导入——
主流程零改动。
"""
from __future__ import annotations

from .base import (
    BaseProvider,
    ProviderFactory,
    parse_host_port,
    parse_optional_float,
    register,
)
from .default_http import DefaultHttpProvider
from .freeproxy import FreeProxyProvider
from .http91 import Http91Provider
from .juliangip import JuliangipDynamicProvider, JuliangipProvider

__all__ = [
    "BaseProvider",
    "DefaultHttpProvider",
    "FreeProxyProvider",
    "Http91Provider",
    "JuliangipDynamicProvider",
    "JuliangipProvider",
    "ProviderFactory",
    "parse_host_port",
    "parse_optional_float",
    "register",
]
