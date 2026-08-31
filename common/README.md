# 公共库（ip_pool_common）

三个独立项目（一级池、二级池、代理层）共享的基础代码库。仅收纳**稳定、通用、与业务无关**的代码，保证高内聚低耦合：业务逻辑一律留在各自项目内，公共库只提供数据模型、代理测试原语、配置加载、日志、API 通用件。

## 目录结构

```
common/
├── README.md
├── pyproject.toml                 # 打包为 ip_pool_common，供三项目依赖
└── ip_pool_common/
    ├── __init__.py                # 统一导出
    ├── models.py                  # 协议枚举、记录/统计数据结构
    ├── testing.py                 # 代理可达性测试 + 站点连通测试原语
    ├── config.py                  # YAML + pydantic-settings 配置加载
    ├── logging_setup.py           # 结构化日志初始化
    └── api.py                     # 统一响应封装、错误码、API 计数器中间件
```

## 依赖关系（依赖方向）

```
common (ip_pool_common)   ← 被依赖（最底层，不依赖任何业务项目）
   ▲         ▲         ▲
   │         │         │
level1_pool  level2_pool  proxy        ← 各自通过 HTTP API 通信，代码零耦合
```

- 三个项目只依赖 `common`，项目之间**不相互 import**。
- 跨项目交互（二级池 → 一级池、代理层 → 二级池）一律走 HTTP API，实现进程级解耦。

## 安装与使用

本项目库按 PEP 517 打包，可在三个项目中以可编辑模式安装：

```bash
pip install -e ../common
```

安装后任意项目内可：

```python
from ip_pool_common import Protocol, IpRecord, proxy_reachability_test, site_test, ...
```

（三项目亦可将本目录加入 `PYTHONPATH` 直接 import，二者等效。）

## 模块说明

### 1. models.py —— 数据模型

```python
class Protocol(StrEnum):
    HTTP = "http"
    HTTPS = "https"
    SOCKS4 = "socks4"
    SOCKS5 = "socks5"

@dataclass
class ProviderIp:            # 供应商返回的规范化结构（供一级池 BaseProvider 输出）
    ip: str
    port: int
    protocol: Protocol
    region: str | None = None
    ttl: float | None = None          # 供应商支持 TTL 时返回（秒）

@dataclass
class IpRecord:              # 一级池记录
    id: int                            # 一级池全局自增，绝不复用
    ip: str
    port: int
    protocol: Protocol
    proxy_url: str                     # 派生字段，如 "http://1.2.3.4:8080"
    region: str | None = None
    ttl: float | None = None
    created_at: float = 0.0
    last_verified_at: float = 0.0

@dataclass
class Level2Record:          # 二级池记录（唯一键为 proxy_url，id 为本地自增）
    id: int                            # 二级池本地 id，唯一不复用（API 引用用）
    ip: str
    port: int
    protocol: Protocol
    proxy_url: str
    region: str | None = None
    ttl: float | None = None
    latency_ms: float = 0.0            # 站点连通测试延迟
    leased: bool = False               # 租赁标记（无过期时间）
    leased_at: float | None = None
    created_at: float = 0.0
    last_verified_at: float = 0.0

def build_proxy_url(ip, port, protocol) -> str   # 工具：组装 proxy_url
```

### 2. testing.py —— 代理测试原语

> 设计要点：测试只关心「能否与代理建立协议会话」，**不验证出口**；站点测试是唯一的出口验证，仅二级池入池时使用。

```python
async def proxy_reachability_test(proxy_url: str, timeout: float = 3.0) -> tuple[bool, float]:
    """只验证能否连接代理（建立代理协议会话），不做任何出口验证。
    - http/https: TCP + 发送 CONNECT 占位目标:443 握手，收到任意合法 HTTP 代理响应(200/4xx/5xx，含407)即判定可用
    - socks4/5: 完成 SOCKS 握手(greeting + CONNECT)，收到任意合法协议应答即判定可用
    返回 (ok, latency_ms)。"""

async def site_test(proxy_url: str, target_url: str, timeout: float = 3.0) -> tuple[bool, float]:
    """经代理真实访问目标站点，验证出口可达。返回 (ok, latency_ms)。
    仅二级池初始入池测试使用。"""

# 需要失败原因（供批次汇总日志，如 timeout*10）时，使用 *_detailed 变体：
# proxy_reachability_test_detailed / site_test_detailed 返回 (ok, latency_ms, reason)。
# reason 为失败原因键（成功为 None）：timeout / connect / proxy_reject /
# http_5xx / invalid_proxy / client_error / exception。
# classify_test_error(exc) 可将任意测试异常归类为上述原因键。

async def batch_test(items, test_fn, concurrency: int = 20) -> list:
    """信号量并发批量测试，仅返回测试通过的项。"""
```

实现依赖：`aiohttp` + `aiohttp-socks`（http/https 用 `ProxyConnector`，socks4/socks5 用 `ProxyConnector.from_url`）。

### 3. config.py —— 配置加载

```python
def load_yaml(path: str) -> dict

def load_settings(settings_cls: type[BaseSettings], path: str, env_prefix: str):
    """YAML 为基底，环境变量(env_prefix 前缀)可覆盖，实例化为 pydantic-settings 对象。
    统一校验与缺省填充，缺失必填项时给出明确报错。"""
```

### 4. logging_setup.py —— 日志

```python
def setup_logging(service_name: str, level: str = "INFO",
                  fmt: str | None = None, log_file: str | None = None):
    """初始化结构化日志：统一时间戳/服务名/级别/模块/消息格式，支持文件与标准输出。"""
```

### 5. api.py —— API 通用件

```python
class ErrorCode(IntEnum):     # 统一错误码
    OK = 0
    NOT_FOUND = 40401
    EMPTY_POOL = 40402
    INTERNAL = 50000
    ...

def ok(data) -> dict                       # {"code":0,"msg":"ok","data":...}
def err(code, msg) -> dict                 # {"code":...,"msg":...,"data":None}

class ApiCounterMiddleware:                # 中间件：统计 API 调用次数
    def __init__(self): ...
    @property
    def count(self) -> int: ...

async def run_app(app, host, port) -> None  # 统一 uvicorn 启动入口
```

## 设计原则

1. **只放真正共享的代码**：一旦某段代码只有单个项目使用，即下沉回该项目，不堆积在公共库。
2. **无状态、无副作用**：公共库代码不持有全局可变状态（中间件计数器除外，且由业务方显式实例化）。
3. **类型完备**：全部使用类型注解 + dataclass/pydantic，接口契约清晰。
4. **可独立打包**：不依赖任何业务项目，可被单机/多机部署场景复用。
