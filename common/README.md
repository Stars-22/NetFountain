# 公共库（ip_pool_common）

三个独立项目（一级池、二级池、代理层）共享的基础代码库。仅收纳**稳定、通用、与业务无关**的代码，保证高内聚低耦合：业务逻辑一律留在各自项目内，公共库只提供数据模型、代理测试原语、配置加载、日志、API 通用件。

## 目录结构

```
common/
├── README.md
├── pyproject.toml                 # 打包为 ip_pool_common，供三项目依赖
└── ip_pool_common/
    ├── __init__.py                # 统一导出（保持旧导出名兼容）
    ├── models.py                  # 协议枚举、记录数据结构
    ├── errors.py                  # 测试错误分类注册表（register_error_rule 扩展点）
    ├── concurrency.py             # 通用并发设施：TickLoop / BoundedTestPipeline / run_periodic
    ├── config.py                  # YAML + pydantic-settings 配置加载 + deep_merge（唯一实现）
    ├── logging_setup.py           # 结构化日志初始化与子池日志拆分
    ├── testing/                   # 代理测试原语
    │   ├── reachability.py        # 纯握手代理可达性（零出口流量）
    │   ├── site.py                # 站点连通（唯一出口验证）
    │   └── batch.py               # 批量并发执行器（batch_test / run_batch_detailed）
    └── api/                       # API 通用件
        ├── codes.py               # ErrorCode 统一错误码
        ├── responses.py           # {code,msg,data} 响应封装
        ├── middleware.py          # 计数/业务码日志中间件（path_prefix+counter 注入）
        └── runner.py              # 统一 uvicorn 启动入口
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

### 2. testing/ —— 代理测试原语

> 设计要点：测试只关心「能否与代理建立协议会话」，**不验证出口**；站点测试是唯一的出口验证，仅二级池入池时使用。子模块：`reachability.py`（纯握手可达性）、`site.py`（站点连通）、`batch.py`（批量并发执行器）。

```python
PROBE_HOST = "placeholder.invalid"   # 可达性探测的占位目标（.invalid 顶级域保证解析失败，
                                     # 代理会快速回 502/拒绝应答，从而仅凭收到合法代理应答判定可达）
PROBE_PORT = 443
PROBE_TARGET = f"http://{PROBE_HOST}:{PROBE_PORT}/"

async def proxy_reachability_test(proxy_url: str, timeout: float = 3.0) -> tuple[bool, float]:
    """只验证能否连接代理（建立代理协议会话），不做任何出口验证。
    通过 python_socks 直接完成「纯握手」（不发内层请求，零出口流量）：
    - http/https: 发送 CONNECT 占位目标:443 并读应答；收到 2xx（隧道建立）或
      4xx/5xx（合法代理应答，含 407 鉴权）即判可用；连接拒绝/超时/无应答判不可达
    - socks4/5: 完成 greeting + CONNECT 握手；握手成功(REP=0x00)即判可用
    纯握手使 lazy-CONNECT 类代理（对任何 CONNECT 立即回 200）也能被正确判定为可达。
    返回 (ok, latency_ms)。session 参数仅保留兼容旧签名，本实现不再使用。"""

async def site_test(proxy_url: str, target_url: str, timeout: float = 3.0) -> tuple[bool, float]:
    """经代理真实访问目标站点，验证出口可达。返回 (ok, latency_ms)。
    收到任意 <500 的 HTTP 响应即判 ok；5xx/连接错误/超时判失败。
    仅二级池初始入池测试使用。"""

# 需要失败原因（供批次汇总日志，如 timeout*10）时，使用 *_detailed 变体：
# proxy_reachability_test_detailed / site_test_detailed 返回 (ok, latency_ms, reason)。
# reason 为失败原因键（成功为 None）：timeout / connect / proxy_reject /
# http_5xx / invalid_proxy / client_error / exception。
# classify_test_error(exc)（errors.py）可将任意测试异常归类为上述原因键。

async def batch_test(items, test_fn, concurrency: int = 20) -> list:
    """信号量并发批量测试，并发不超过 concurrency，仅返回测试通过的项，保持原顺序。"""

async def run_batch_detailed(items, test_fn, concurrency: int = 20) -> list[tuple]:
    """扩展批量执行器：返回与输入等长保序的 (item, ok, latency, reason) 全量结果
    （含失败项与原因键），供需要汇总失败构成的调用方（如二级池 Tester）复用。"""
```

实现依赖：`aiohttp` + `aiohttp-socks` + `python-socks`——`proxy_reachability_test` 的握手**直接经 `python_socks` 完成**（兼容 lazy-CONNECT 代理，不依赖 aiohttp-socks）；`site_test` 使用 `aiohttp-socks` 的 `ProxyConnector`（http/https 用 `ProxyConnector`，socks4/socks5 用 `ProxyConnector.from_url`）。`python-socks` 通过 `aiohttp-socks` 传递依赖引入。

### 3. errors.py —— 测试错误分类注册表

```python
def classify_test_error(error: BaseException) -> str:
    """按注册顺序将异常归类为原因键：timeout/connect/proxy_reject/invalid_proxy/
    client_error，全部不匹配返回 "exception"。"""

def register_error_rule(exc_types: tuple[type[BaseException], ...], reason: str,
                        *, position: int | None = None) -> None:
    """开闭扩展点：注册新的「异常类型 → 原因键」规则，无需修改分类器本体。
    默认插入到兜底之前（优先级最低的内置规则之后），position 可显式指定。"""

def reset_error_rules() -> None:
    """恢复内置规则（供测试隔离）。"""
```

内置规则保持既有判定顺序（先超时、再连接、后代理协议/业务类异常），依赖异常继承层级。

### 4. concurrency.py —— 通用并发设施

> 供 level1_pool / level2_pool 复用，消除各自重复的「队列 + worker + drop-oldest」与「sleep → 执行 → 异常兜底」样板。

```python
def resolve_worker_count(explicit: int | None, concurrency: int, expected_batch: int) -> int:
    """默认 max(1, concurrency // expected_batch)；显式值超上限时截断。"""

class TickLoop:
    """按 tick 起点计时的周期循环：慢 tick 不拖累节奏，单次异常回调 on_error（不中断）。"""

async def run_periodic(action, *, interval, on_error=None, sleep_fn=None) -> None:
    """先休眠后执行的周期任务（TTL 清扫、复验等）；单次异常不中断循环。"""

class BoundedTestPipeline:
    """有界队列 + 固定 worker 池的批处理管线（拉取与处理解耦）。
    - enqueue：队满丢弃最旧批次（内存有界），累计 drops 并回调 on_drop；
    - run_supervised(driver)：启动 worker 运行 driver，结束后回收 worker；
    - run_worker / queue / drops / join 供细粒度驱动与观测。"""
```

### 5. config.py —— 配置加载

```python
def load_yaml(path: str) -> dict

def load_settings(settings_cls: type[BaseSettings], path: str, env_prefix: str):
    """YAML 为基底，环境变量(env_prefix 前缀)可覆盖，实例化为 pydantic-settings 对象。
    统一校验与缺省填充，缺失必填项时给出明确报错。"""

def deep_merge(base: dict, override: dict) -> dict:
    """全系统唯一 deep_merge 实现：递归合并、标量覆盖、不改入参。
    多供应商/多子池配置装配共用（此前三个项目各有一份拷贝）。"""
```

### 6. logging_setup.py —— 日志

```python
def setup_logging(service_name: str, level: str = "INFO",
                  fmt: str | None = None, log_file: str | None = None):
    """初始化结构化日志：统一时间戳/服务名/级别/模块/消息格式，支持文件与标准输出。"""
```

### 7. api/ —— API 通用件

> 子模块：`codes.py`（错误码）、`responses.py`（响应封装）、`middleware.py`（ASGI 中间件）、`runner.py`（启动入口）。

```python
class ErrorCode(IntEnum):     # 统一错误码（codes.py）
    OK = 0
    PARAM_ERROR = 40000
    NOT_FOUND = 40400         # 站点未配置 / 对象不存在
    EMPTY_POOL = 40402        # 二级池 acquire 空池
    INTERNAL = 50000
    UPSTREAM_ERROR = 50200    # 代理层转发上游故障

def ok(data, **extra) -> dict             # {"code":0,"msg":"ok","data":data,**extra}
                                          # extra 附加顶层字段（如增量接口 max_id）
def err(code, msg) -> dict                # {"code":...,"msg":...,"data":None}

class ApiCounterMiddleware:               # middleware.py：调用计数（ASGI，thread/async 安全）
    def __init__(self, app, *, path_prefix=None, counter=None): ...
    # path_prefix：仅统计匹配前缀的 http 请求（如 "/api/v1"）；
    # counter(scope)：命中时的计数回调（统计落点由装配方注入，无需子类化）；
    # 均为 None 时旧行为：统计全部 http/websocket，经 count 属性读取。

class BizCodeLogMiddleware:               # middleware.py：每请求追加含返回业务码的访问日志
    def __init__(self, app): ...          # 格式 http=<状态码> biz=<业务码> method=<方法> path=<路径>
                                          # body 非 JSON 时业务码记 "-"；不改响应、不吞异常

async def run_app(app, host, port) -> None  # runner.py：统一 uvicorn 启动入口
```

## 设计原则

1. **只放真正共享的代码**：一旦某段代码只有单个项目使用，即下沉回该项目，不堆积在公共库。
2. **无状态、无副作用**：公共库代码不持有全局可变状态（中间件计数器除外，且由业务方显式实例化）。
3. **类型完备**：全部使用类型注解 + dataclass/pydantic，接口契约清晰。
4. **可独立打包**：不依赖任何业务项目，可被单机/多机部署场景复用。
