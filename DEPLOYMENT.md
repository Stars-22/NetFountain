# NetFountain 部署使用说明

本文档仅覆盖 **NetFountain 两级代理 IP 池系统**的部署（环境准备、依赖安装、配置、启动、验证、运维），不涉及业务 API 的调用方式。API 使用请参考各项目 `项目策划书.md` 与跨服务契约（见根目录 `README.md`）。

## 1. 项目简介

NetFountain 是一个两级代理 IP 池系统，由四个目录组成：

| 目录 | 角色 | 默认端口 | 说明 |
|---|---|---|---|
| `common` | 公共库 `ip_pool_common` | — | 数据模型、代理测试原语、配置加载、日志、API 通用件。被三业务项目依赖 |
| `level1_pool` | 一级池 | 8000 | 从供应商拉取 IP → 代理可达性测试 → 入环形池（上限 + TTL 淘汰） |
| `level2_pool` | 二级池 | 8001+ | 从一级池增量同步 → 站点连通测试（<2000ms）→ 租赁池。**每站点一份配置、一个独立进程、一个独立端口** |
| `proxy` | 代理层 | 9000 | 按站点标识路由到对应二级池并纯透传请求/响应，每分钟热更新路由表 |

```
  供应商公网 IP 池 (HTTP)
         │
         ▼
  ┌──────────────────────────────┐
  │      一级池 level1_pool       │   端口 8000
  │       (0.0.0.0:8000)         │
  └──────────────┬───────────────┘
                 │ HTTP 增量同步
       ┌─────────┴─────────┐
       ▼                   ▼
  ┌────────────┐    ┌────────────┐
  │ 二级池 A    │    │ 二级池 B    │   每站点独立进程，端口 8001 / 8002 / ...
  │ (:8001)    │    │ (:8002)    │
  └─────┬──────┘    └─────┬──────┘
        │ HTTP            │ HTTP
        └────────┬────────┘
                 ▼
  ┌──────────────────────────────┐
  │      代理层 proxy (:9000)     │   端口 9000
  └──────────────┬───────────────┘
                 │ HTTP
                 ▼
               用户
```

三个业务项目是**相互独立**的服务，代码零耦合，仅通过 HTTP API 通信。

## 2. 环境要求

- **Python**：≥ 3.11（项目以 Python 3.14 开发验证，推荐使用 3.14）。
- **操作系统**：Linux / Windows / macOS 均可（无平台相关代码）。
- **网络**：
  - 部署一级池的机器需能访问代理供应商 API（默认 `http://api.91http.com/v1/get-ip`）。
  - 部署二级池的机器需能访问一级池（`level1.base_url`）以及被代理访问的目标站点（`site.target_url`）。
  - 部署代理层的机器需能访问各二级池（`sites[].base_url`）。
  - 多机部署时各服务间按需互相连通，并放行对应端口防火墙。

## 3. 获取代码

```bash
git clone <仓库地址> NetFountain
cd NetFountain
```

## 4. 依赖安装

三个业务项目的 `requirements.txt` 均以 **editable 方式**引用公共库（`-e ../common`）。安装任意项目的依赖会一并把 `ip_pool_common` 以可编辑模式装入当前 Python 环境。建议使用虚拟环境。

### 方式一（推荐）：逐项目安装

```bash
# 一级池
pip install -r level1_pool/requirements.txt

# 二级池
pip install -r level2_pool/requirements.txt

# 代理层
pip install -r proxy/requirements.txt
```

### 方式二：先装公共库再装各项目

```bash
pip install -e ../common        # 或 pip install -e ./common
pip install -r level1_pool/requirements.txt
pip install -r level2_pool/requirements.txt
pip install -r proxy/requirements.txt
```

> 说明：依赖均为无版本号约束安装（FastAPI、uvicorn、aiohttp、aiohttp-socks、pydantic、pydantic-settings、PyYAML）。如需要测试，另装各项目的 `requirements-dev.txt`（pytest、pytest-asyncio、pytest-cov、aioresponses、httpx 等）。

验证公共库安装成功：

```bash
python -c "import ip_pool_common; print(ip_pool_common.__file__)"
```

## 5. 配置说明

配置机制统一为：**YAML 为基底，环境变量可覆盖**，最终实例化为 pydantic-settings 对象。环境变量规则为 `服务前缀 + __` + 嵌套路径，例如 `LEVEL1_SERVICE__PORT=8080` 覆盖 `service.port`。三个服务的前缀分别是 `LEVEL1_`、`LEVEL2_`、`PROXY_`。

> 配置文件缺失时，服务会回退到代码内默认值（仍受环境变量影响）。各服务启动时自动读取 `app/config.py` 同目录上级 `config/<name>.yaml`。

### 5.1 一级池 `level1_pool/config/level1_pool.yaml`

```yaml
service:
  host: 0.0.0.0            # 监听地址
  port: 8000               # 监听端口
  log_level: INFO

provider:
  type: http91             # 供应商类型：http91 | default_http
  api_url: http://api.91http.com/v1/get-ip   # 供应商拉取地址
  api_key: <你的密钥>        # 供应商密钥（91HTTP 为 secret）
  trade_no: <你的业务编号>   # 供应商业务编号（91HTTP 专用）
  protocol: 1              # 91HTTP：1=HTTP，2=SOCKS5
  pull_count: 10           # 每次拉取数量
  pull_interval: 1.0       # 拉取间隔（秒）
  pull_timeout: 5.0        # 拉取超时（秒）
  supports_ttl: true       # 供应商是否返回过期时间（91HTTP 支持）

pool:
  max_size: 500            # 环形池容量上限

test_timeout: 3.0          # 代理可达性测试超时（秒）
test_concurrency: 10       # 测试并发数
ttl_sweep_interval: 5.0    # TTL 过期清理周期（秒）
```

要点：

- **凭据入库**：`level1_pool/config/level1_pool.yaml` 属运行期本地文件（已 gitignore），请从模板 `config/level1_pool.example.yaml` 复制并替换 `api_key` / `trade_no` 为**你自己的** 91HTTP 凭据，确保不入库、不泄露。
- `provider.type` 当前支持两种：
  - `http91`：适配 91HTTP `/v1/get-ip` JSON 接口（携带 `expire_time` 折算 TTL）。
  - `default_http`：通用 HTTP 供应商，GET `api_url`（携带 `api_key`），解析 `{data:[{ip,port,protocol,region,ttl}]}` 格式，用于自建/联调供应商。
- 新增供应商只需在 `level1_pool/app/provider.py` 中「继承 `BaseProvider` + `@register("类型名")`」，无需改主流程。

### 5.2 二级池 `level2_pool/config/level2_pool.yaml`

> **重要提醒**：仓库中现有 `level2_pool/config/level2_pool.yaml` 是**测试残留配置**（端口 8111、站点名 `ttl`、指向本地 mock 站点 `http://127.0.0.1:9100/`）。**正式部署必须用模板 `config/level2_pool.example.yaml` 改写**，否则会启动一个连到本机 mock 服务的错误实例。

模板 `config/level2_pool.example.yaml` 内容：

```yaml
service:
  host: 0.0.0.0
  port: 8001               # 每个站点使用独立端口
  log_level: INFO

site:
  name: site_a             # 站点标识（代理层路由用，需与 proxy_routes.yaml 一致）
  target_url: http://www.baidu.com   # 该站点连通测试的目标 URL

level1:
  base_url: http://127.0.0.1:8000    # 一级池地址（多机部署时填一级池机器 IP）

sync:
  interval: 3.0            # 增量同步周期（秒）
  timeout: 5.0             # 同步超时（秒）

test:
  latency_threshold_ms: 2000   # 站点连通延迟阈值，>2000ms 不入池
  connect_timeout: 3.0         # 连通测试超时（秒）
  concurrency: 20              # 测试并发数

revalidate_interval: 60.0      # 周期复验间隔（秒）
ttl_sweep_interval: 5.0        # TTL 过期清理周期（秒）
```

要点：

- **每站点一套**：每个站点复制一份配置为 `level2_pool/config/level2_pool.yaml`，修改 `site.name`、`site.target_url`、`service.port`，然后以该进程目录启动一个实例。站点名全局唯一，作为代理层的路由键。
- 配置里的 `site.name` 必须与代理层 `proxy_routes.yaml` 中 `sites[].name` 完全一致（大小写敏感）。
- 单机部署时 `level1.base_url` 用 `127.0.0.1`；多机部署时填一级池所在机器 IP。

### 5.3 代理层 `proxy/config/proxy_routes.yaml`

```yaml
service:
  host: 0.0.0.0
  port: 9000
  log_level: INFO

registry:
  route_file: config/proxy_routes.yaml   # 本地路由表文件
  route_url: ""                          # 或远端路由表 URL（两者二选一，URL 优先）
  reload_interval: 60.0                  # 路由表热更新周期（秒）

dispatch:
  timeout: 10.0                          # 到二级池的透传超时（秒）

sites:                                   # 站点路由表：site → 二级池地址
  - name: site_a
    base_url: http://127.0.0.1:8001
    target_url: https://www.example.com
  - name: site_b
    base_url: http://127.0.0.1:8002
    target_url: https://www.example.org
```

要点：

- 路由表有两种来源：本地文件（`route_file`）或远端 URL（`route_url`）。`route_file` 相对路径基于项目根目录解析；`route_url` 便于集中管理多机路由。
- 代理层启动时加载路由表，之后每 `reload_interval` 秒**热更新**一次；更新失败保留旧表。**新增站点只需编辑路由表，无需重启代理层**（等待一个重载周期生效）。
- `sites[].base_url` 指向对应二级池进程地址；多机部署时填二级池机器 IP。

## 6. 启动服务

建议按 **一级池 → 二级池 → 代理层** 的顺序启动。二级池依赖一级池可用（首次全量同步）；代理层可随时启动，但站点未配置或对应二级池不可达时会返回错误。

> 说明：`uvicorn` 的 `--host`/`--port` 参数只是监听参数；服务实际使用的配置以 `config/*.yaml` 为准（如端口、站点、上游地址）。`--app-dir <目录>` 使 uvicorn 从对应项目目录加载 `app.main`。

### 6.1 一级池（端口 8000）

```bash
uvicorn app.main:app --app-dir level1_pool --host 0.0.0.0 --port 8000
```

确认 `level1_pool/config/level1_pool.yaml` 中 `service.port` 为 8000（或与 `--port` 一致）。启动后开始周期性拉取并测试代理。

### 6.2 二级池（每站点一个实例）

站点 A（配置文件 `level2_pool/config/level2_pool.yaml` 中 `port: 8001`）：

```bash
uvicorn app.main:app --app-dir level2_pool --host 0.0.0.0 --port 8001
```

站点 B（复制配置，改 `site.name`/`target_url`/`port: 8002`）：

```bash
uvicorn app.main:app --app-dir level2_pool --host 0.0.0.0 --port 8002
```

> 多站点部署时，请把各站点配置分别放在各自进程可访问的 `level2_pool/config/level2_pool.yaml` 位置。每个实例必须使用各自的配置（站点名、端口不同）。

### 6.3 代理层（端口 9000）

```bash
uvicorn app.main:app --app-dir proxy --host 0.0.0.0 --port 9000
```

### 6.4 多机部署要点

- 各服务 `service.host` 保持 `0.0.0.0`（监听所有网卡），让对端机器可访问。
- 把下游地址从 `127.0.0.1` 改为对端机器 IP：
  - 二级池配置中的 `level1.base_url` → 一级池机器 IP。
  - 代理层 `proxy_routes.yaml` 中的 `sites[].base_url` → 各二级池机器 IP。
- 在每台机器防火墙 / 云安全组放行对应端口（8000 / 8001+ / 9000）。

## 7. 部署验证

启动后用浏览器或 `curl` 做冒烟健康检查（仅检查服务存活，不涉及业务调用）：

```bash
# 代理层健康检查（含当前已加载的路由表）
curl http://<proxy-host>:9000/api/v1/health

# 一级池状态（含池大小、拉取统计）
curl http://<level1-host>:8000/api/v1/status

# 二级池状态（含池统计、最近同步 id）
curl http://<level2-host>:8001/api/v1/status
```

成功标准：

- 各服务返回 `{"code":0, ...}`，`code=0` 表示正常。
- 代理层 `/api/v1/health` 的 `data.sites` 中能看到配置的全部站点，`data.started_at` / `data.stats` 展示代理层启动时间与 API 被调用次数。
- 一级池 `status` 的 `pool_size` 随时间增长（供应商可用时）。
- 二级池 `status` 的 `pool_stats.total` 随时间增长，`last_synced_id` 持续前进（说明与一级池同步正常）。

## 8. 运维与排障

### 8.1 进程守护

三服务均为独立 uvicorn 进程，建议用 systemd（Linux）或 supervisor / 云平台进程托管，实现开机自启与崩溃重启。示例（systemd unit，以一级池为例）：

```ini
[Unit]
Description=NetFountain Level1 Pool
After=network.target

[Service]
WorkingDirectory=/path/to/NetFountain
ExecStart=/path/to/python -m uvicorn app.main:app --app-dir level1_pool --host 0.0.0.0 --port 8000
Restart=always
User=netfountain

[Install]
WantedBy=multi-user.target
```

### 8.2 日志

- 三个服务默认结构化日志输出到标准输出（`log_level` 在各自配置 `service.log_level` 中调整，服务启动时通过 `setup_logging` 生效）。用 systemd 可经 `journalctl -u <unit>` 查看，或由进程托管重定向到文件。
- 常见日志关键字：一级池 `pull from ... failed`（供应商拉取失败）、二级池同步相关警告、代理层路由重载信息。
- **每条 API 请求会额外输出一条带业务码的访问日志**（在 uvicorn 默认访问日志之后），格式为 `http=<HTTP状态码> biz=<业务码> method=<方法> path=<路径>`，如：

  ```
  INFO:     127.0.0.1:61234 - "POST /api/v1/site_a/ips/42/release HTTP/1.1" 200 OK
  2026-08-29 12:00:00 INFO proxy [ip_pool_common.api] http=200 biz=40400 method=POST path=/api/v1/site_a/ips/42/release
  ```

  `biz` 即响应 body 的 `code` 字段（`0` 成功，`40400`/`40402`/`50200` 等为业务错误），HTTP 状态码与业务码可据此同时观测，便于对「HTTP 成功但业务失败」的请求告警/排障。响应 body 非 JSON 时 `biz` 记 `-`。

### 8.3 常见问题

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 一级池 `pool_size` 一直为 0 | 供应商 API 不可达 / 凭据错误 / 拉取数量为 0 | 检查网络与 `provider.api_url`、`api_key`、`trade_no`；看日志 `pull from ... failed` |
| 二级池 `pool_stats.total` 为 0 | 一级池地址错误 / 一级池空 / 站点连通测试全部超时 | 检查 `level1.base_url`；确认一级池有 IP；确认 `site.target_url` 可经代理访问 |
| 二级池 `last_synced_id` 不增长 | 增量同步异常（越界时二级池会自动触发全量重拉） | 检查一级池 `/api/v1/ips/after/{id}` 是否正常；看同步日志 |
| 代理层返回 `40400`（site not configured） | 请求站点未在路由表配置 / 路由表未重载 | 检查 `proxy_routes.yaml` `sites` 条目与站点名；等待一个 `reload_interval` |
| 代理层返回 `50200`（upstream error） | 对应二级池未启动或不可达 | 启动/检查二级池；确认 `sites[].base_url` 正确 |
| 二级池启动后连到错误站点 | 使用了测试残留的 `level2_pool.yaml` | 用 `level2_pool.example.yaml` 模板重写配置 |

### 8.4 停止服务

直接终止各 uvicorn 进程即可。优雅停止：发送 `SIGINT`（Ctrl+C）或 `SIGTERM`，FastAPI lifespan 会取消后台任务并释放 aiohttp 会话。

## 9. 附：测试运行（可选）

如需在部署前自测（不影响正式服务）：

```bash
# 公共库
pip install -e ../common && cd common && pytest -v --cov=ip_pool_common --cov-report=term-missing

# 各业务项目
pip install -r <项目>/requirements-dev.txt
cd <项目> && pytest -v --cov=app --cov-report=term-missing

# 端到端（会拉起真实服务链，含 mock 供应商/站点）
cd e2e && pip install -r requirements.txt && pytest
```

> 端到端测试默认跳过性能用例，需显式运行：`pytest -m perf`。