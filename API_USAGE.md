# NetFountain API 使用说明

本文档仅列出本系统三个服务的全部 HTTP API 及其详细用法（字段、参数、示例、错误码），不包含架构与部署说明。

## 1. 总览

### 1.1 服务与端口

| 服务 | 默认端口 | API 前缀 |
|---|---|---|
| 一级池 `level1_pool` | 8000 | `/api/v1` |
| 二级池 `level2_pool` | 8001（默认配置 8111） | `/api/v1` |
| 代理层 `proxy` | 9000 | `/api/v1` |

### 1.2 统一响应结构

所有服务返回统一信封：

```json
{ "code": 0, "msg": "ok", "data": <任意> }
```

- `code = 0` 表示成功；非 0 为错误码。
- 失败时 `data` 为 `null`。

### 1.3 错误码表

| code | 名称 | 含义 |
|---|---|---|
| 0 | `OK` | 成功 |
| 40000 | `PARAM_ERROR` | 参数错误 |
| 40400 | `NOT_FOUND` | 站点未配置 / 对象不存在 |
| 40402 | `EMPTY_POOL` | 二级池 acquire 时空池或全部已租赁 |
| 50000 | `INTERNAL` | 内部错误 |
| 50200 | `UPSTREAM_ERROR` | 代理层上游转发失败 |

### 1.4 协议枚举

`protocol` 字段取值：`http`、`https`、`socks4`、`socks5`。

### 1.5 代理层透传规则

- 代理层为纯透传网关，不定义业务字段。
- `/{site}` 路径段表示站点标识，转发前被剥离：`/api/v1/{site}/ips/acquire` → 转发至该站点二级池的 `/api/v1/ips/acquire`。
- 请求的方法、查询参数、JSON 请求体原样转发；上游的 HTTP 状态码与响应体原样透传。
- 路由表（站点 → 上游基础 URL）每分钟热更新，见 `proxy/config/proxy_routes.yaml`。

| 情况 | HTTP 状态 | 响应体 |
|---|---|---|
| 站点未配置 | 404 | `{"code":40400,"msg":"site not configured","data":null}` |
| 上游不可达/超时 | 502 | `{"code":50200,"msg":"upstream error","data":null}` |
| 上游正常 | 上游状态码 | 上游响应体原样 |

> **日志中的业务码**：三服务启动后按 `service.log_level` 输出结构化日志，且每条 API 请求追加一行含返回业务码的访问日志，格式 `http=<HTTP状态码> biz=<业务码> method=<方法> path=<路径>`。`biz` 即响应 body 的 `code` 字段，便于同时观测 HTTP 状态与业务结果（如 `HTTP 200` + `biz=40400` 表示记录不存在）。

---

## 2. 一级池 API（level1_pool, :8000）

一级池共 4 个接口，全部为 GET、无参数、无请求体。

### 2.1 GET /api/v1/status

服务状态与统计快照。

**请求**：无参数、无请求体。

**响应 `data` 字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `uptime` | float | 进程启动至今秒数 |
| `total_pulled` | int | 从供应商拉取总数 |
| `total_entered` | int | 通过可达性测试入池总数 |
| `total_duplicates` | int | 因 `proxy_url` 重复被跳过更新（仅 TTL 变小且 region 未变）的累计次数 |
| `pool_size` | int | 当前池容量 |
| `counts` | dict | 各协议数量 `{http, https, socks4, socks5}` |
| `api_call_count` | int | 累计 `/api/v1` 调用次数 |
| `next_id` | int | 全局自增 ID 水位 |
| `errors` | object | 错误计数 `{pull_failures, test_failures, ttl_sweep_failures}` |
| `drops` | int | 因队满被丢弃的待测批次累计数 |

**示例**：

```json
{
  "code": 0, "msg": "ok",
  "data": {
    "uptime": 1234.567,
    "total_pulled": 1000,
    "total_entered": 800,
    "total_duplicates": 12,
    "pool_size": 500,
    "counts": {"http": 300, "https": 100, "socks4": 50, "socks5": 50},
    "api_call_count": 42,
    "next_id": 812,
    "errors": {"pull_failures": 3, "test_failures": 0, "ttl_sweep_failures": 0},
    "drops": 0
  }
}
```

### 2.2 GET /api/v1/count

仅返回池容量与各协议数量。

**请求**：无参数、无请求体。

**响应 `data` 字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `pool_size` | int | 当前池容量 |
| `counts` | dict | 各协议数量 `{http, https, socks4, socks5}` |

**示例**：

```json
{
  "code": 0, "msg": "ok",
  "data": {
    "pool_size": 500,
    "counts": {"http": 300, "https": 100, "socks4": 50, "socks5": 50}
  }
}
```

### 2.3 GET /api/v1/ips

返回池内全部 IP 记录（按插入顺序）。

**请求**：无参数、无请求体。

**响应 `data`**：记录数组，每条字段如下（空池返回 `[]`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | 全局自增 ID |
| `ip` | str | IP 地址 |
| `port` | int | 端口 |
| `protocol` | str | 协议枚举 |
| `proxy_url` | str | 代理地址 `<protocol>://<ip>:<port>` |
| `region` | str\|null | 地区 |
| `ttl` | float\|null | 生存时间 |
| `created_at` | float | 创建时间戳 |

**示例**：

```json
{
  "code": 0, "msg": "ok",
  "data": [
    {
      "id": 1, "ip": "1.2.3.4", "port": 8080,
      "protocol": "http", "proxy_url": "http://1.2.3.4:8080",
      "region": "CN", "ttl": 3600.0, "created_at": 1690000000.0
    }
  ]
}
```

### 2.4 GET /api/v1/ips/after/{id}

增量同步接口：返回 `id` 之后（`id > {id}`）的记录。响应顶层带 `max_id`（当前池内最大 id，池空为 `null`）。

**请求**：
- 路径参数 `id`（int，必填）：增量水位 ID。

**响应 `data`**：与 `/api/v1/ips` 相同的记录数组；另含顶层字段 `max_id`。

**增量为空时的二级池判定**：
- `id == max_id`：一级池暂无新 IP，**不做全量提取**（主池尚未有新进 IP 时不触发全量重拉）；
- `id > max_id`：一级池 id 空间已重置（重启/换代），触发全量重拉并重置水位线；
- `max_id` 缺失（旧版一级池）：回退全量重拉。

**示例**：

```
GET /api/v1/ips/after/100
```

```json
{
  "code": 0, "msg": "ok",
  "max_id": 102,
  "data": [
    { "id": 101, "ip": "5.6.7.8", "port": 3128, "protocol": "https",
      "proxy_url": "https://5.6.7.8:3128", "region": "US", "ttl": 7200.0,
      "created_at": 1690000600.0 }
  ]
}
```

---

## 3. 二级池 API（level2_pool, :8001+）

二级池共 7 个接口。每条记录字段结构如下（`/api/v1/ips` 及 `acquire` 返回）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | 本池本地记录 ID |
| `ip` | str | IP 地址 |
| `port` | int | 端口 |
| `protocol` | str | 协议枚举 |
| `proxy_url` | str | 代理地址 |
| `latency_ms` | float | 站点连通测试延迟（毫秒） |
| `leased` | bool | 是否已租赁 |
| `ttl` | float\|null | 生存时间 |
| `created_at` | float | 创建时间戳 |

### 3.1 GET /api/v1/status

服务运行统计与池统计。

**请求**：无参数、无请求体。

**响应 `data` 字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `uptime` | float | 进程启动至今秒数 |
| `total_pulled` | int | 从一级池拉取总数 |
| `total_entered` | int | 通过站点连通测试入池总数 |
| `api_call_count` | int | 累计 `/api/v1` 调用次数 |
| `last_synced_id` | int\|null | 增量同步水位 |
| `errors` | object | 错误计数 `{sync_failures, test_failures, revalidate_failures, ttl_sweep_failures, empty_acquires}` |
| `drops` | int | 因队满被丢弃的待测批次累计数 |
| `pool_stats` | object | 池统计，见下 |

`pool_stats` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `total` | int | 池内总数 |
| `by_proto` | dict | 各协议总数 `{http, https, socks4, socks5}` |
| `leased_total` | int | 已租赁总数 |
| `leased_by_proto` | dict | 各协议已租赁数 |
| `free_total` | int | 空闲总数 |
| `free_by_proto` | dict | 各协议空闲数 |

**示例**：

```json
{
  "code": 0, "msg": "ok",
  "data": {
    "uptime": 100.0, "total_pulled": 800, "total_entered": 600,
    "api_call_count": 10, "last_synced_id": 799,
    "errors": {"sync_failures": 1, "test_failures": 0, "revalidate_failures": 0,
               "ttl_sweep_failures": 0, "empty_acquires": 0},
    "drops": 0,
    "pool_stats": {
      "total": 600,
      "by_proto": {"http": 300, "https": 150, "socks4": 80, "socks5": 70},
      "leased_total": 200,
      "leased_by_proto": {"http": 100, "https": 50, "socks4": 30, "socks5": 20},
      "free_total": 400,
      "free_by_proto": {"http": 200, "https": 100, "socks4": 50, "socks5": 50}
    }
  }
}
```

### 3.2 GET /api/v1/count

仅返回池统计（同 `/api/v1/status` 中的 `pool_stats` 结构）。

**请求**：无参数、无请求体。

**示例**：

```json
{
  "code": 0, "msg": "ok",
  "data": {
    "total": 600,
    "by_proto": {"http": 300, "https": 150, "socks4": 80, "socks5": 70},
    "leased_total": 200,
    "leased_by_proto": {"http": 100, "https": 50, "socks4": 30, "socks5": 20},
    "free_total": 400,
    "free_by_proto": {"http": 200, "https": 100, "socks4": 50, "socks5": 50}
  }
}
```

### 3.3 GET /api/v1/ips

返回池内全部记录（按插入顺序，不改变租赁状态）。

**请求**：无参数、无请求体。

**响应 `data`**：记录数组（结构见本节开头），空池返回 `[]`。

**示例**：

```json
{
  "code": 0, "msg": "ok",
  "data": [
    { "id": 1, "ip": "1.2.3.4", "port": 8080, "protocol": "http",
      "proxy_url": "http://1.2.3.4:8080", "latency_ms": 120.5,
      "leased": false, "ttl": 3600.0, "created_at": 1690000000.0 }
  ]
}
```

### 3.4 POST /api/v1/ips/acquire

租赁一条代理：租赁最新的空闲记录（按插入顺序从尾部向前扫描），标记 `leased=true` 并设置 `leased_at`。租赁操作在锁内原子执行。

**请求**：无参数、无请求体。

**响应**：
- 成功：HTTP 200，`data` 为被租赁的记录，`leased: true`。
- 失败（空池或全部已租赁）：HTTP 200，`code: 40402`，`msg: "empty pool: no free ip available"`，`data: null`。

**示例（成功）**：

```json
{
  "code": 0, "msg": "ok",
  "data": {
    "id": 10, "ip": "9.9.9.9", "port": 1080, "protocol": "socks5",
    "proxy_url": "socks5://9.9.9.9:1080", "latency_ms": 88.0,
    "leased": true, "ttl": 3600.0, "created_at": 1690001000.0
  }
}
```

**示例（失败）**：

```json
{ "code": 40402, "msg": "empty pool: no free ip available", "data": null }
```

### 3.5 POST /api/v1/ips/{id}/release

释放某条记录的租赁（`leased=false`，`leased_at=null`）。

**请求**：
- 路径参数 `id`（int，必填）：本池本地记录 ID。

**响应**：
- 成功：HTTP 200，`data: true`。
- 失败（记录不存在）：HTTP 200，`code: 40400`，`msg: "record not found: {id}"`，`data: null`。

**示例（成功）**：

```json
{ "code": 0, "msg": "ok", "data": true }
```

### 3.6 DELETE /api/v1/ips/{id}

从池中彻底删除一条记录（连同其租赁状态）。

**请求**：
- 路径参数 `id`（int，必填）：本池本地记录 ID。

**响应**：
- 成功：HTTP 200，`data: true`。
- 失败（记录不存在）：HTTP 200，`code: 40400`，`msg: "record not found: {id}"`，`data: null`。

**示例（成功）**：

```json
{ "code": 0, "msg": "ok", "data": true }
```

### 3.7 POST /api/v1/ips/release-all

释放所有当前已租赁的记录。

**请求**：无参数、无请求体。

**响应 `data`**：int，本次释放的租赁数量。

**示例**：

```json
{ "code": 0, "msg": "ok", "data": 3 }
```

---

## 4. 代理层 API（proxy, :9000）

代理层共 8 个接口，除 `/api/v1/health` 外均为透传接口（`{site}` 为路由表中的站点标识）。透传接口的响应与上游二级池一致，见上文各对应接口。

### 4.1 GET /api/v1/health

网关存活检查，返回当前路由表、代理层自身运行统计，并实时聚合一级池与各站点二级池的 `/status` 信息（`pools`）。

**请求**：无参数、无请求体。

**响应 `data` 字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | str | 固定 `"ok"` |
| `started_at` | str | 代理层启动时间（ISO 8601 UTC，如 `2026-08-29T12:00:00Z`） |
| `uptime` | float | 自启动以来的运行秒数 |
| `stats` | object | 代理层 API 被调用统计（见下） |
| `sites` | array | 站点列表，每项 `{name, base_url, target_url}` |
| `pools` | object | 实时聚合的池状态（见下） |

`stats` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `total_calls` | int | 代理层 API 被调用总次数（含 health 自身） |
| `calls_by_ip` | object | 按来源客户端 IP 的调用次数 `{ip: count}` |
| `calls_by_site` | object | 按站点透传转发次数 `{site: count}` |
| `errors` | object | 代理层自身错误次数 `{code: count}`，仅含代理层传输错误（`40400` 站点未配置 / `50200` 上游不可达）；**不统计**任何二级池业务错误码（如 `40402` 空池，由二级池自身 `/status` 的 `errors` 统计） |

`pools` 字段（health 时实时请求一级池与各站点二级池 `/status`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `pools.level1` | object | `{base_url, status}`；`status` 为一级池 `/status` 的 `data`，不可达时为 `{error}` |
| `pools.sites` | array | 各站点 `{name, base_url, status}`；`status` 为该站点二级池 `/status` 的 `data`，不可达时为 `{error}` |

**示例**：

```json
{
  "code": 0, "msg": "ok",
  "data": {
    "status": "ok",
    "started_at": "2026-08-29T12:00:00Z",
    "uptime": 123.456,
    "stats": {
      "total_calls": 5,
      "calls_by_ip": { "127.0.0.1": 5 },
      "calls_by_site": { "site_a": 2, "site_b": 2 },
      "errors": { "40400": 1 }
    },
    "sites": [
      { "name": "site_a", "base_url": "http://127.0.0.1:8001", "target_url": null },
      { "name": "site_b", "base_url": "http://127.0.0.1:8002", "target_url": null }
    ],
    "pools": {
      "level1": {
        "base_url": "http://127.0.0.1:8000",
        "status": { "pool_size": 500, "total_pulled": 1000 }
      },
      "sites": [
        { "name": "site_a", "base_url": "http://127.0.0.1:8001",
          "status": { "total": 600, "free_total": 400 } },
        { "name": "site_b", "base_url": "http://127.0.0.1:8002",
          "status": { "error": "unreachable" } }
      ]
    }
  }
}
```

### 4.2 GET /api/v1/{site}/status

透传 → 上游二级池 `GET /api/v1/status`。查询参数原样转发。

### 4.3 GET /api/v1/{site}/count

透传 → 上游二级池 `GET /api/v1/count`。查询参数原样转发。

### 4.4 GET /api/v1/{site}/ips

透传 → 上游二级池 `GET /api/v1/ips`。查询参数原样转发。

### 4.5 POST /api/v1/{site}/ips/acquire

透传 → 上游二级池 `POST /api/v1/ips/acquire`。请求体为原始 JSON 原样转发（可为空）；成功返回被租赁记录，空池时返回 `code: 40402`。

### 4.6 POST /api/v1/{site}/ips/{id}/release

透传 → 上游二级池 `POST /api/v1/ips/{id}/release`。

**路径参数**：
- `site`（str，必填）：站点标识。
- `id`（int，必填）：本池本地记录 ID（非 int 时返回 422 校验错误）。

请求体可选原始 JSON，原样转发。成功返回 `data: true`，记录不存在返回 `code: 40400`。

### 4.7 DELETE /api/v1/{site}/ips/{id}

透传 → 上游二级池 `DELETE /api/v1/ips/{id}`。

**路径参数**：
- `site`（str，必填）：站点标识。
- `id`（int，必填）：本池本地记录 ID。

成功返回 `data: true`，记录不存在返回 `code: 40400`。

### 4.8 POST /api/v1/{site}/ips/release-all

透传 → 上游二级池 `POST /api/v1/ips/release-all`。请求体可选原始 JSON，原样转发；成功返回释放数量 `data: <int>`。

---

## 5. 接口速查表

### 一级池（level1_pool, :8000）

| 方法 | 路径 | 参数 | 请求体 | data |
|---|---|---|---|---|
| GET | `/api/v1/status` | — | — | 服务+池统计 |
| GET | `/api/v1/count` | — | — | 池统计 |
| GET | `/api/v1/ips` | — | — | 记录数组 |
| GET | `/api/v1/ips/after/{id}` | `id`(int) | — | 记录数组（顶层含 `max_id`） |

### 二级池（level2_pool, :8001+）

| 方法 | 路径 | 参数 | 请求体 | data | 错误 |
|---|---|---|---|---|---|
| GET | `/api/v1/status` | — | — | 服务+池统计 | — |
| GET | `/api/v1/count` | — | — | 池统计 | — |
| GET | `/api/v1/ips` | — | — | 记录数组 | — |
| POST | `/api/v1/ips/acquire` | — | — | 记录(leased=true) | 40402 |
| POST | `/api/v1/ips/{id}/release` | `id`(int) | — | `true` | 40400 |
| DELETE | `/api/v1/ips/{id}` | `id`(int) | — | `true` | 40400 |
| POST | `/api/v1/ips/release-all` | — | — | 释放数量(int) | — |

### 代理层（proxy, :9000）

| 方法 | 路径 | 参数 | 请求体 | data |
|---|---|---|---|---|
| GET | `/api/v1/health` | — | — | 路由表+代理层统计 |
| GET | `/api/v1/{site}/status` | `site` | — | 上游透传 |
| GET | `/api/v1/{site}/count` | `site` | — | 上游透传 |
| GET | `/api/v1/{site}/ips` | `site` | — | 上游透传 |
| POST | `/api/v1/{site}/ips/acquire` | `site` | 原始 JSON | 上游透传 |
| POST | `/api/v1/{site}/ips/{id}/release` | `site`,`id`(int) | 原始 JSON | 上游透传 |
| DELETE | `/api/v1/{site}/ips/{id}` | `site`,`id`(int) | — | 上游透传 |
| POST | `/api/v1/{site}/ips/release-all` | `site` | 原始 JSON | 上游透传 |
