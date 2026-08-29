# NetFountain 使用说明（代理层网关 API）

本手册面向**使用方（用户/调用方）**，说明如何通过**代理层网关**（Proxy Gateway，默认端口 `9000`）从代理 IP 池中获取 IP 并使用。

代理层是对外开放的**唯一取 IP 入口**：它按站点标识 `{site}` 把请求透传到对应二级池，并将上游响应原样返回。用户无需关心一级池 / 二级池内部实现。

> 本手册不含部署 / 启动相关内容。默认端口与站点标识以实际部署为准。

---

## 1. 概念速览

| 概念 | 说明 |
|---|---|
| 基路径 | `/api/v1` |
| `{site}` | 站点标识（路由键），代理层按它路由到对应二级池，如 `site_a`、`site_b` |
| `proxy_url` | 可直接使用的代理地址，格式 `{protocol}://{ip}:{port}`，如 `http://1.2.3.4:8080` |
| `{id}` | 二级池记录在站点内的本地自增 id，用于 `release` / `delete` 精确引用 |

**代理协议枚举**：`http`、`https`、`socks4`、`socks5`。

**典型调用链**：

```
GET/POST /api/v1/{site}/ips/acquire  ──►  代理层 :9000  ──►  站点对应二级池  ──►  返回 proxy_url
```

---

## 2. 快速上手：从池中获取 IP 并实际使用

### 步骤 0：确认池中有货

```bash
curl http://127.0.0.1:9000/api/v1/site_a/count
```

返回示例：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "total": 5,
    "by_proto": {"http": 2, "https": 1, "socks4": 1, "socks5": 1},
    "leased_total": 2,
    "leased_by_proto": {"https": 1, "socks5": 1},
    "free_total": 3,
    "free_by_proto": {"http": 2, "socks4": 1}
  }
}
```

关注 `free_total`（空闲数）。若为 `0`，则 `acquire` 会返回空池错误（见下文）。

### 步骤 1：获取一个空闲 IP

```bash
curl -X POST http://127.0.0.1:9000/api/v1/site_a/ips/acquire
```

成功返回（`data` 即一条**已租赁**记录）：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "id": 3,
    "ip": "1.2.3.4",
    "port": 8080,
    "protocol": "http",
    "proxy_url": "http://1.2.3.4:8080",
    "latency_ms": 350.0,
    "leased": true,
    "ttl": 120.0,
    "created_at": 1766880000.0
  }
}
```

取用 `data.proxy_url` 作为代理地址，并**记下 `data.id`** 供后续释放。

### 步骤 2：用代理发起请求

**curl（HTTP 代理）**

```bash
curl -x http://1.2.3.4:8080 https://api.example.com/data
# 等价写法：
curl --proxy http://1.2.3.4:8080 https://api.example.com/data
```

**curl（SOCKS5 代理）**

```bash
curl --socks5 1.2.3.4:1080 https://api.example.com/data
```

**Python aiohttp（HTTP 代理）**

```python
import aiohttp

proxy_url = "http://1.2.3.4:8080"  # 来自 acquire 返回的 data.proxy_url

async with aiohttp.ClientSession() as s:
    async with s.get("https://api.example.com/data", proxy=proxy_url) as resp:
        print(resp.status, await resp.text())
```

**Python aiohttp（SOCKS5 代理，需 aiohttp-socks）**

```python
from aiohttp_socks import ProxyConnector
import aiohttp

connector = ProxyConnector.from_url("socks5://1.2.3.4:1080")

async with aiohttp.ClientSession(connector=connector) as s:
    async with s.get("https://api.example.com/data") as resp:
        print(resp.status, await resp.text())
```

### 步骤 3：使用完毕，释放（归还池中，供他人再次获取）

```bash
curl -X POST http://127.0.0.1:9000/api/v1/site_a/ips/3/release
```

返回：

```json
{"code": 0, "msg": "ok", "data": true}
```

> 租赁**没有过期时间**，只有显式 `release` / `delete` / `release-all` 才会解除。拿到 IP 后请务必在合适时机释放，避免长期占用。

---

## 3. 统一约定

### 3.1 统一响应结构

所有代理层 API 返回统一 JSON 结构：

```json
{
  "code": 0,        // 业务码，0 为成功
  "msg": "ok",      // 描述
  "data": null      // 业务数据，可为任意类型
}
```

`GET /api/v1/health` 与各透传端点均遵循此结构；透传端点把上游二级池的 `{code, msg, data}` **原样返回**，代理层不加工、不缓存任何 IP / 租赁数据。

### 3.2 业务错误码

| code | 含义 | 说明 |
|---|---|---|
| 0 | 成功 | `msg="ok"` |
| 40000 | 参数错误 | `PARAM_ERROR` |
| 40400 | 站点未配置 / 对象不存在 | 请求的 `{site}` 不在代理层路由表中；或 `release/delete` 的 `{id}` 不存在 |
| 40402 | 空池 | `acquire` 时无空闲 IP（`free_total=0`） |
| 50000 | 内部错误 | `INTERNAL` |
| 50200 | 上游故障 | 代理层转发到上游二级池时不可达 / 超时 / 响应异常 |

### 3.3 HTTP 状态码

- 透传端点：HTTP 状态码沿用上游二级池返回的状态码（正常业务场景为 `200`）。
- `{site}` 未配置：HTTP `404`，body 为 `{"code": 40400, "msg": "site not configured", "data": null}`。
- 上游二级池不可达 / 超时：HTTP `502`，body 为 `{"code": 50200, "msg": "upstream error", "data": null}`。

**判断成功请以 body 中的 `code == 0` 为准**，而非仅看 HTTP 状态码。

---

## 4. 代理层 API 详情

代理层共 9 个对外端点。除 `/health` 外，其余 8 个为按站点透传端点，与二级池 API 一一对应。

### 4.1 健康检查与路由表

`GET /api/v1/health`

返回代理层自身状态（启动时间、API 被调用次数等）与当前已加载的站点路由表（不含站点池数据）。

请求示例：

```bash
curl http://127.0.0.1:9000/api/v1/health
```

响应示例：

```json
{
  "code": 0,
  "msg": "ok",
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
      {"name": "site_a", "base_url": "http://127.0.0.1:8001", "target_url": "https://www.example.com"},
      {"name": "site_b", "base_url": "http://127.0.0.1:8002", "target_url": "https://www.example.org"}
    ]
  }
}
```

`started_at` 为代理层启动时间（ISO 8601 UTC），`uptime` 为运行秒数；`stats` 为代理层 API 被调用统计（`total_calls` 总次数、`calls_by_ip` 按来源客户端 IP、`calls_by_site` 按站点转发、`errors` 错误响应计数）。统计为进程内累计，进程重启后归零。

`sites[].name` 即 `{site}` 取值；`target_url` 为该站点二级池连通性测试所面向的目标站点。

### 4.2 查询类端点

#### GET /api/v1/{site}/status — 站点运行状态

透传到二级池 `/api/v1/status`。返回该站点的运行统计与池内统计。

请求示例：

```bash
curl http://127.0.0.1:9000/api/v1/site_a/status
```

响应示例（`data`）：

```json
{
  "uptime": 3600.5,
  "total_pulled": 1000,
  "total_entered": 880,
  "api_call_count": 42,
  "last_synced_id": 137,
  "pool_stats": {
    "total": 20,
    "by_proto": {"http": 8, "https": 6, "socks4": 3, "socks5": 3},
    "leased_total": 5,
    "leased_by_proto": {"http": 2, "https": 2, "socks5": 1},
    "free_total": 15,
    "free_by_proto": {"http": 6, "https": 4, "socks4": 3, "socks5": 2}
  }
}
```

| 字段 | 说明 |
|---|---|
| `uptime` | 服务运行秒数 |
| `total_pulled` | 从一级池累计拉取的 IP 数 |
| `total_entered` | 通过站点连通测试并入池的累计数 |
| `api_call_count` | 该站点累计被调用的次数 |
| `last_synced_id` | 当前同步水位线（一级池记录 id） |
| `pool_stats` | 池内统计，见下表 |

`pool_stats` 字段：

| 字段 | 说明 |
|---|---|
| `total` | 池内 IP 总数 |
| `by_proto` | 池内各协议数量，如 `{"http": 8, ...}` |
| `leased_total` | 已租赁总数 |
| `leased_by_proto` | 已租赁的各协议数量 |
| `free_total` | 空闲总数（可供 `acquire` 的数量） |
| `free_by_proto` | 空闲的各协议数量 |

#### GET /api/v1/{site}/count — 池内计数

透传到二级池 `/api/v1/count`。返回与 `status` 中 `pool_stats` 相同结构的计数，不含运行统计。

请求示例：

```bash
curl http://127.0.0.1:9000/api/v1/site_a/count
```

响应示例（`data`）：

```json
{
  "total": 20,
  "by_proto": {"http": 8, "https": 6, "socks4": 3, "socks5": 3},
  "leased_total": 5,
  "leased_by_proto": {"http": 2, "https": 2, "socks5": 1},
  "free_total": 15,
  "free_by_proto": {"http": 6, "https": 4, "socks4": 3, "socks5": 2}
}
```

#### GET /api/v1/{site}/ips — 全部 IP 列表

透传到二级池 `/api/v1/ips`。返回该站点池内**全部**记录（含已租赁），**不产生租赁标记**，仅供查看。

请求示例：

```bash
curl http://127.0.0.1:9000/api/v1/site_a/ips
```

响应示例（`data` 为数组）：

```json
[
  {
    "id": 1,
    "ip": "1.2.3.4",
    "port": 8080,
    "protocol": "http",
    "proxy_url": "http://1.2.3.4:8080",
    "latency_ms": 350.0,
    "leased": true,
    "ttl": 120.0,
    "created_at": 1766880000.0
  },
  {
    "id": 2,
    "ip": "5.6.7.8",
    "port": 1080,
    "protocol": "socks5",
    "proxy_url": "socks5://5.6.7.8:1080",
    "latency_ms": 420.0,
    "leased": false,
    "ttl": null,
    "created_at": 1766880100.0
  }
]
```

| 字段 | 说明 |
|---|---|
| `id` | 站点内本地自增 id，供 `release` / `delete` 引用 |
| `ip` / `port` | 代理 IP 与端口 |
| `protocol` | 协议，`http` / `https` / `socks4` / `socks5` |
| `proxy_url` | 可直接使用的代理地址 |
| `latency_ms` | 最近一次站点连通测试延迟（毫秒） |
| `leased` | 是否已被租赁 |
| `ttl` | 存活时间（秒）；供应商未提供时为 `null`，仅保留池内，TTL 过期由后台自动淘汰 |
| `created_at` | 入池时间戳（Unix 秒） |

### 4.3 租赁类端点

#### POST /api/v1/{site}/ips/acquire — 获取一个空闲 IP

透传到二级池 `/api/v1/ips/acquire`。从池中**最新优先**（后入池的先分配）取一个空闲 IP，**原子标记为已租赁**并返回。

请求示例：

```bash
curl -X POST http://127.0.0.1:9000/api/v1/site_a/ips/acquire
```

成功响应（`data` 为单条记录，`leased=true`）：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "id": 3,
    "ip": "1.2.3.4",
    "port": 8080,
    "protocol": "http",
    "proxy_url": "http://1.2.3.4:8080",
    "latency_ms": 350.0,
    "leased": true,
    "ttl": 120.0,
    "created_at": 1766880000.0
  }
}
```

空池（`free_total=0`）返回（HTTP `200`，以 body 的 code 判断）：

```json
{
  "code": 40402,
  "msg": "empty pool: no free ip available",
  "data": null
}
```

> 高并发下同一 IP 不会重复分配（二级池在锁内原子操作）。空池时可稍后重试，二级池会周期性从一级池同步补货。

#### POST /api/v1/{site}/ips/{id}/release — 释放指定 IP

透传到二级池 `/api/v1/ips/{id}/release`。解除 `{id}` 对应记录的租赁，使其回到空闲池。

请求示例：

```bash
curl -X POST http://127.0.0.1:9000/api/v1/site_a/ips/3/release
```

成功响应：

```json
{"code": 0, "msg": "ok", "data": true}
```

`{id}` 不存在（HTTP `200`）：

```json
{"code": 40400, "msg": "record not found: 3", "data": null}
```

#### DELETE /api/v1/{site}/ips/{id} — 删除指定 IP

透传到二级池 `DELETE /api/v1/ips/{id}`。将 `{id}` 对应记录从池中**永久删除**（含解除租赁），删除后不可恢复。

请求示例：

```bash
curl -X DELETE http://127.0.0.1:9000/api/v1/site_a/ips/3
```

成功响应：

```json
{"code": 0, "msg": "ok", "data": true}
```

`{id}` 不存在：`{"code": 40400, "msg": "record not found: 3", "data": null}`。

#### POST /api/v1/{site}/ips/release-all — 释放全部

透传到二级池 `/api/v1/ips/release-all`。解除该站点池内**全部**租赁，`data` 为实际解除数量。

请求示例：

```bash
curl -X POST http://127.0.0.1:9000/api/v1/site_a/ips/release-all
```

响应示例：

```json
{"code": 0, "msg": "ok", "data": 5}
```

---

## 5. 端点一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查 + 站点路由表 |
| GET | `/api/v1/{site}/status` | 站点运行状态与池内统计 |
| GET | `/api/v1/{site}/count` | 池内计数（空闲/租赁/按协议） |
| GET | `/api/v1/{site}/ips` | 全部 IP 列表（不标记） |
| POST | `/api/v1/{site}/ips/acquire` | 获取 1 个空闲 IP 并租赁 |
| POST | `/api/v1/{site}/ips/{id}/release` | 释放指定 IP |
| DELETE | `/api/v1/{site}/ips/{id}` | 删除指定 IP |
| POST | `/api/v1/{site}/ips/release-all` | 释放全部 |

---

## 6. 使用建议

- **获取 / 释放配对**：租赁无过期时间，`acquire` 后请在业务结束或代理失效时 `release`（或必要时 `delete`），避免长期占用空闲池。
- **判断成功看 `code`**：空池（40402）等业务错误 HTTP 仍为 `200`，请以 body 的 `code == 0` 判断结果。
- **空池重试**：`acquire` 遇 40402 说明当前无空闲 IP，可稍后重试；二级池会周期性从一级池同步并补入新 IP。
- **`{site}` 一致性**：`{site}` 必须与代理层路由表中的站点名一致（见 `/health` 返回），否则返回 HTTP `404` / `40400`。
- **`{id}` 作用域**：`id` 是站点内本地自增值，仅在同一个 `{site}` 下有效；不同站点的 id 互不通用。
- **直连说明**：本手册统一经代理层网关访问。若需直接访问二级池，其 API 与本手册 4.2 / 4.3 节完全一致，仅去掉 `{site}` 段（例如 `POST http://127.0.0.1:8001/api/v1/ips/acquire`），但对外推荐统一走代理层。