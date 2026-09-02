# NetFountain —— 两级代理 IP 池系统

一个两级代理 IP 池系统，由四个目录组成：`common`（公共库）、`level1_pool`（一级池）、`level2_pool`（二级池）、`proxy`（代理层）。三个业务项目是独立服务，均依赖公共库 `ip_pool_common`，项目之间不相互 import，仅通过 HTTP API 通信。

## 系统架构

```
  供应商公网 IP 池 (HTTP, 1s/10个)
         │
         ▼
 ┌──────────────────────────────┐
 │      一级池 level1_pool       │  拉取(91HTTP/供应商) → 代理可达性测试 → 环形池(500+TTL)
 │       (0.0.0.0:8000)         │  拉取与测试解耦 · proxy_url 去重 · HTTP API /api/v1
 └──────────────┬───────────────┘
                │ HTTP 增量同步 (/api/v1/ips, /ips/after/{id})
      ┌─────────┴─────────┐
      ▼                   ▼
 ┌────────────┐    ┌────────────┐
 │ 二级池 A    │    │ 二级池 B    │   每站点独立进程
 │ level2_pool│    │ level2_pool│   同步 → 站点连通测试(<2000ms) → 租赁池
 │ (:8001)    │    │ (:8002)    │
 └─────┬──────┘    └─────┬──────┘
       │ HTTP            │ HTTP
       └────────┬────────┘
                ▼
 ┌──────────────────────────────┐
 │      代理层 proxy (:9000)     │  按站点路由 → 透传请求/响应
 │      /api/v1/{site}/...      │  每分钟重载路由表
 └──────────────┬───────────────┘
                │ HTTP
                ▼
              用户
```

## 目录职责

| 目录 | 职责 | 端口 |
|---|---|---|
| `common` | 公共库 `ip_pool_common`：数据模型、代理测试原语、配置加载、日志、API 通用件。被三项目依赖，不依赖任何业务项目。 | — |
| `level1_pool` | 一级池：从供应商（91HTTP / default_http）拉取 IP、代理可达性测试（拉取与测试解耦多 worker）、按 `proxy_url` 去重入环形池、TTL/容量淘汰、对外查询 API。 | 8000 |
| `level2_pool` | 二级池：从一级池增量同步、站点连通测试（延迟 < 2000ms 才入池）、租赁分配/释放/删除、周期复验。每站点一份配置、独立进程。 | 8001+ |
| `proxy` | 代理层：按站点标识路由到对应二级池并纯透传请求/响应，每分钟热更新路由表。 | 9000 |
| `frontend` | Web 面板 + 数据聚合后端 BFF（Vue3 + Express5 + node:sqlite）：只经 HTTP 定时聚合三服务数据并提供可视化。 | 3000/5173 |

## 三服务安装与启动

各项目先安装依赖（公共库以 editable 方式引用），再以 uvicorn 启动。

```bash
# 一级池
pip install -r level1_pool/requirements.txt
uvicorn app.main:app --app-dir level1_pool --host 0.0.0.0 --port 8000

# 二级池（每站点一份配置，端口独立）
pip install -r level2_pool/requirements.txt
uvicorn app.main:app --app-dir level2_pool --host 0.0.0.0 --port 8001

# 代理层
pip install -r proxy/requirements.txt
uvicorn app.main:app --app-dir proxy --host 0.0.0.0 --port 9000
```

## 文档索引

### 使用说明

| 文档 | 内容 | 适用对象 |
|---|---|---|
| `USAGE.md` | 使用说明：代理层网关对外 API，从池中获取 / 释放 / 删除 IP 的完整流程 | 使用方（用户 / 调用方） |
| `API_USAGE.md` | API 使用说明：三个服务全部 HTTP API（字段、参数、示例、错误码） | 开发者 / 运维 |
| `DEPLOYMENT.md` | 部署使用说明：环境准备、依赖安装、配置、启动、验证、运维排障 | 部署 / 运维 |

### 项目文档

- 公共库：`common/README.md`、`common/测试计划书.md`
- 一级池：`level1_pool/项目策划书.md`、`level1_pool/测试计划书.md`
- 二级池：`level2_pool/项目策划书.md`、`level2_pool/测试计划书.md`
- 代理层：`proxy/项目策划书.md`、`proxy/测试计划书.md`
- 前端面板 + BFF：`frontend/README.md`（技术文档：架构、BFF 设计、API、前端设计、约束）

## 跨服务契约要点

- **统一响应结构**：所有服务返回 `{code, msg, data}`。`code=0` 表示成功；非 0 为错误码（如 40402 空池、40400 站点未配置）。
- **一级池 → 二级池**：`GET /api/v1/ips` 返回全部 IP；`GET /api/v1/ips/after/{id}` 返回 `id` 之后（增量）的 IP，响应顶层带 `max_id`（当前池内最大 id）。增量返回为空时，二级池仅当水位线已越过 `max_id`（一级池重启/换代）才触发全量重拉；水位线等于 `max_id` 视为暂无新 IP，不做全量提取。
- **一级池去重**：一级池按 `proxy_url`（ip+port+protocol）去重，重复入池删除旧记录并以新 id 重建（刷新 ttl/region）；`/status` 暴露 `total_duplicates` 累计次数，新 id 会驱动二级池增量同步感知刷新。
- **代理层 → 二级池**：`/api/v1/{site}/...` 剥离 `{site}` 段后透传到对应二级池，`{code,msg,data}` 原样透传。
- **协议枚举**：`http`、`https`、`socks4`、`socks5`。
- **公共库引用**：`requirements.txt` 中 `-e ../common`（editable install），安装后 `import ip_pool_common`。
