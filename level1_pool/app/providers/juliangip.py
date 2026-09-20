"""巨量IP（juliangip）供应商：不限量 / 动态（包时/包量）代理提取接口。

- 不限量代理：GET http://v2.api.juliangip.com/unlimited/getips
- 动态（包时/包量）代理：GET http://v2.api.juliangip.com/dynamic/getips

接口文档：https://www.juliangip.com/help/api/unlimited/
          https://www.juliangip.com/help/api/dynamic/
签名规则：https://www.juliangip.com/help/api/sign/

两接口响应结构一致（result_type=json）：

    {
      "code": 200,
      "msg": "请求成功",
      "data": {
        "count": 5,
        "filter_count": 0,
        "surplus_quantity": 985,
        "proxy_list": [
          "61.145.245.78:54873",
          "27.153.143.162:35525"
        ]
      }
    }

- ``trade_no``（业务编号）来自 ``cfg.trade_no``，``key``（API Key）来自 ``cfg.api_key``；
- ``num`` 取请求 ``count``（接口上限 100），``pt`` 取 ``cfg.protocol``（1=HTTP，2=SOCK）；
- ``ip_remain=1``（默认开启）时每条结果形如 ``ip:port,剩余秒数``，换算为
  ``ProviderIp.ttl``；关闭时不携带该参数，ttl 恒为 None；
- ``sign`` 为「全部请求参数按参数名字典序拼接为 ``k=v&k=v`` 串，末尾追加
  ``&key=<api_key>``」的 32 位小写 MD5；空值参数不参与签名；
- 动态（包时/包量）接口额外支持 ``area``（地区）、``isp``（运营商）、``filter``
  （过滤今日已提取 IP）筛选；
- ``code != 200``（如 401 签名校验失败）仅记日志并返回空列表；网络/超时/HTTP/解析
  异常抛出（基类统一处理）。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from ip_pool_common.models import Protocol, ProviderIp

from .base import (
    BaseProvider,
    items_list,
    parse_host_port,
    parse_optional_float,
    payload_dict,
    register,
)

logger = logging.getLogger(__name__)

__all__ = ["JuliangipDynamicProvider", "JuliangipProvider", "sign_params"]


def sign_params(params: dict[str, Any], key: str) -> str:
    """按巨量签名规则计算 sign：参数名字典序拼接（跳过 sign/空值）+ ``&key=<key>``，MD5 小写。

    示例（官方文档）：``city_name=1&ip_remain=1&num=10&result_type=json&
    trade_no=1178311789392776&key=99064631962e4e838dac1143092f6112``
    → ``8f35c3e56bf640cb2597ea2492ca62db``。
    """
    items = sorted(
        (str(k), str(v))
        for k, v in params.items()
        if k != "sign" and v is not None and str(v).strip() != ""
    )
    raw = "&".join(f"{k}={v}" for k, v in items)
    return hashlib.md5(f"{raw}&key={key}".encode("utf-8")).hexdigest()


def _split_entry(item: Any) -> tuple[str, int, float | None] | None:
    """解析一条代理记录为 ``(ip, port, ttl)``；非法项返回 None。

    - 字符串格式：``ip:port`` 或 ``ip:port,剩余秒数``（ip_remain=1 时的逗号后缀）；
    - 兼容 dict 格式：``{ip, port, ip_remain}``。
    """
    if isinstance(item, dict):
        hp = parse_host_port(item)
        if hp is None:
            return None
        return hp[0], hp[1], parse_optional_float(item.get("ip_remain"))
    if not isinstance(item, str):
        return None
    parts = [p.strip() for p in item.strip().split(",")]
    host_port = parts[0] if parts else ""
    ip, sep, port_text = host_port.rpartition(":")
    if not sep or not ip:
        return None
    try:
        port = int(port_text)
    except (TypeError, ValueError):
        return None
    ttl = parse_optional_float(parts[1]) if len(parts) > 1 else None
    return ip, port, ttl


@register("juliangip")
class JuliangipProvider(BaseProvider):
    """巨量IP（不限量代理）供应商。"""

    MAX_COUNT = 100  # 接口限制：单次提取数量最大 100

    def _base_params(self, count: int) -> dict[str, str]:
        """构造未签名的请求参数（子类可追加产品专属筛选参数后再签名）。"""
        params: dict[str, str] = {
            "trade_no": self.cfg.trade_no,
            "num": str(min(count, self.MAX_COUNT)),
            "pt": str(self.cfg.protocol),
            "result_type": "json",
        }
        if self.cfg.ip_remain:
            params["ip_remain"] = "1"
        return params

    def _params(self, count: int) -> dict[str, str]:
        params = self._base_params(count)
        params["sign"] = sign_params(params, self.cfg.api_key)
        return params

    def _parse(self, payload: Any, count: int) -> list[ProviderIp]:
        data = payload_dict(payload)
        if data is None:
            return []
        code = data.get("code")
        if str(code).strip() != "200":
            logger.warning("provider error: code=%r msg=%r", code, data.get("msg"))
            return []
        body = data.get("data")
        if not isinstance(body, dict):
            logger.warning("provider payload missing 'data' object")
            return []
        proxy_list = items_list(body, "proxy_list")
        if proxy_list is None:
            return []
        protocol = Protocol.SOCKS5 if self.cfg.protocol == 2 else Protocol.HTTP
        out: list[ProviderIp] = []
        for item in proxy_list[:count]:
            parsed = _split_entry(item)
            if parsed is None:
                continue
            ip, port, ttl = parsed
            out.append(ProviderIp(ip=ip, port=port, protocol=protocol, ttl=ttl))
        return out


@register("juliangip_dynamic")
class JuliangipDynamicProvider(JuliangipProvider):
    """巨量IP 动态（包时/包量）代理供应商：GET /dynamic/getips。

    响应解析与签名规则同不限量接口，额外支持 ``area``（地区）、``isp``（运营商）、
    ``filter``（过滤今日已提取 IP）筛选；筛选参数参与签名。
    """

    def _base_params(self, count: int) -> dict[str, str]:
        params = super()._base_params(count)
        if self.cfg.area:
            params["area"] = self.cfg.area
        if self.cfg.isp:
            params["isp"] = self.cfg.isp
        if self.cfg.filter_ip:
            params["filter"] = "1"
        return params
