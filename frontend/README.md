# NetFountain 前端面板 + 数据聚合后端（BFF）

Web 控制台与数据聚合层，用于可视化观测 [NetFountain](https://github.com/anomalyco/NetFountain)（两级代理 IP 池系统）的运行状态与历史趋势。本目录是自包含子项目，仅通过 HTTP 访问 NetFountain 三个服务，**不修改其后端代码**。

## 1. 架构

```
NetFountain 三个服务（Python/FastAPI）
   ├─ 一级池 level1_pool   :8000   /api/v1/*
   ├─ 二级池 level2_pool   :8001+  /api/v1/*（每站点一份）
   └─ 代理层 proxy         :9000   /api/v1/*

        │  BFF 采集（定时轮询 + 800ms 超时）+ 写操作代理（release-all）
        ▼
聚合后端 BFF（本目录 server/，Express 5 + node:sqlite）
   :3000  ── /api/*（统一 {code,msg,data}）

        │  开发：Vite proxy /api → :3000；生产：同进程托管 dist/
        ▼
Vue 前端（本目录 src/，Vue3 + Vite + TS + Element Plus + ECharts + Pinia）
   :5173（dev）/ :3000（生产同源）
```

**铁律：前端只请求 `/api/*`，禁止直接访问 NetFountain 的 8000/8001/9000 端口。** 所有数据（含唯一的写操作 release-all）一律经 BFF。

## 2. 目录结构

```
frontend/
├── package.json              # dev/build/start 脚本 + 依赖
├── vite.config.ts            # proxy /api → http://localhost:3000
├── tsconfig.json             # 前端 TS（vue-tsc 类型检查）
├── index.html
├── .gitignore                # node_modules/ dist/ netfountain.db*
├── server/                   # ===== 聚合后端 BFF（纯 JS ESM，node 直接运行）=====
│   ├── config.js             # 全部可调项（采集周期/超时/保留/端口/地址）
│   ├── db.js                 # node:sqlite 打开、WAL、建表、prepared 语句、降采样、清理
│   ├── collector.js          # 采集循环 + 内存态缓存 + 指标/快照落库
│   ├── util.js               # num/int/protoMap/latencyStats/avgRemainingSeconds 共享工具
│   └── server.js             # Express 5：/api 路由 + release-all 代理 + 生产托管 dist/（唯一入口）
└── src/                      # ===== Vue 前端 =====
    ├── main.ts               # createApp + pinia + router + Element Plus + 暗色 css
    ├── App.vue               # 布局（el-menu 导航 + 暗色开关 + 错误横幅）
    ├── router.ts             # 4 条路由
    ├── api.ts                # fetch 封装（GET/POST，仅 /api/*，解析 {code,msg,data}）
    ├── types.ts              # API 响应 TS 类型
    ├── format.ts             # 格式化 + 延迟着色 + 剩余时间 + PALETTE 调色板 + ERROR_LABELS
    ├── charts.ts             # ECharts option 构造（line/bar/stacked/pie，折线含 dataZoom）
    ├── stores/
    │   ├── app.ts            # 暗色模式（localStorage 持久化）
    │   ├── data.ts           # 可变周期轮询：overview/sites/distributions/stats + 页面刷新钩子
    │   └── refresh.ts        # 自动刷新周期（1/2/5/10/30/60s，默认 5s，localStorage 持久化）
    ├── components/
    │   ├── BaseChart.vue     # ECharts 封装（暗色重载 + resize）
    │   ├── BaseCardCell.vue  # 横条 label/value 单元格（总览/站点/统计共用）
    │   └── RefreshSelector.vue # 自动刷新周期选择器（总览/站点/统计共用）
    └── views/
        ├── Overview.vue      # 总览：按池横条（一级池 + 各二级池各一条）
        ├── Ips.vue           # IP 列表（筛选 + 分页 + 延迟着色 + 剩余时间列）
        ├── Sites.vue         # 站点视图（一级/二级池 Tab + 基础信息 + 健康指标 + 图表 + 一键释放）
        └── Stats.vue         # 统计分析（代理层统计 + calls_by_ip/site + 多色历史折线）
```

## 3. 技术栈

- **BFF**：Node.js（本机 v24.18）+ Express 5 + `node:sqlite`（Node 内置 `DatabaseSync`，**零外部依赖、零原生编译**；等价于 better-sqlite3 的同步 prepared statement API）。
- **前端**：Vue 3 + Vite + TypeScript + Element Plus + ECharts + Pinia + vue-router。
- 无 Nginx/Docker/MySQL/Redis，无 WebSocket/SSE（BFF 仅 REST）。

## 4. 快速开始

```bash
cd frontend

# 开发：concurrently 一键同起 BFF(3000) 与 Vite(5173)
npm install
npm run dev

# 生产：构建后由同一个 server/server.js 托管 dist/ 并提供 /api
npm run build
npm start          # → http://localhost:3000
```

- 依赖 Node ≥ 22.5（`node:sqlite` 稳定版要求；本机 24.18）。
- 每次改动后必须 `npm run build`（vue-tsc 类型检查 + vite 打包）零报错，再 `node server/server.js` 自检。

## 5. BFF 设计

### 5.1 配置（server/config.js）

| 键 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `port` | 3000 | `BFF_PORT` | BFF 对外端口 |
| `level1Url` | `http://127.0.0.1:8000` | `LEVEL1_URL` | 一级池地址 |
| `proxyUrl` | `http://127.0.0.1:9000` | `PROXY_URL` | 代理层地址（站点列表从 `/health` 动态发现） |
| `collectIntervalMs` | 2000 | `COLLECT_INTERVAL_MS` | metrics 聚合表写入周期 |
| `snapshotIntervalMs` | 30000 | `SNAPSHOT_INTERVAL_MS` | ip_snapshots 全量快照周期 |
| `fetchTimeoutMs` | 800 | `FETCH_TIMEOUT_MS` | 单次采集超时即中断 |
| `retentionDays` | 10 | `RETENTION_DAYS` | 数据保留天数（每日 0 点清理） |
| `dbFile` | `netfountain.db` | `DB_FILE` | SQLite 文件（落在 frontend/ 根目录） |

### 5.2 存储（server/db.js）

启动时 `PRAGMA journal_mode = WAL;` + `synchronous = NORMAL`。两张表（索引均 `(site, ts)`）：

**`metrics`**（聚合指标，每 `collectIntervalMs` 写一行；`site` 取值 `level1` / 站点名 / `global`）：
`ts, site, pool_capacity, available_count, leased_count, avg_latency, min_latency, max_latency, by_proto(JSON), total_pulled, total_entered, total_duplicates, pull_failures, test_failures, sync_failures, revalidate_failures, ttl_sweep_failures, empty_acquires, drops`

**`ip_snapshots`**（全量 IP 快照，每 `snapshotIntervalMs` 写一批，避免秒级全量写放大）：
`ts, site, proxy_url, protocol, region, latency_ms, status, ttl, created_at`

写入全部走 `db.prepare(...)` + `BEGIN/COMMIT` 事务。数据保留：`scheduleRetention()` 在每日 0 点及之后每 24h 执行 `DELETE WHERE ts < now - retentionDays*86400`。

### 5.3 采集循环（server/collector.js）

- **自调度 async 循环**（`setTimeout` 链，避免 `setInterval` 重叠）。
- 每轮 `Promise.allSettled` 并发请求，每个请求带 `AbortController` 800ms 超时：
  - `GET level1/api/v1/status`、`GET level1/api/v1/ips`
  - `GET proxy/api/v1/health`（得到站点列表 + 代理层统计）
  - 对 `health.sites` 每个站点：`GET {base_url}/api/v1/status`、`GET {base_url}/api/v1/ips`
- 解析 `{code,msg,data}`：`code!==0` / HTTP 非 200 / 超时 → 记日志并**跳过该项，循环继续**（绝不因单次失败中断）。
- **落库策略**：每轮写 `metrics`（level1 + 各站点 + global 三行）；每 `snapshotIntervalMs` 才写一次 `ip_snapshots`。
- **内存态缓存**（`state`）：最新一轮的 `level1`/`level1Ips`/`proxy`/`sites`，供 `/api/overview`、`/api/distributions`、`/api/stats`、`/api/ips`、release-all 的站点发现直接读取，**不查历史表**。

### 5.4 历史降采样（server/db.js `queryHistory`）

`/api/history` 按时间范围选择桶宽，SQL 用 `CAST(ts / bucket AS INTEGER) * bucket` 分组，**绝不把秒级原始行返回前端**：

| range | 桶宽 | 说明 |
|---|---|---|
| `1h` | 60s | 分钟聚合 |
| `6h` | 300s | 5 分钟聚合 |
| `24h` | 600s | 10 分钟聚合 |
| `7d` | 3600s | 小时聚合 |

每个桶内：`pool_capacity/available_count/avg_latency` 取 `AVG`；`total_pulled/total_entered/total_duplicates` 取 `MAX-MIN` 得增量；各错误计数取 `MAX-MIN` 得桶内增量。查询窗口 `sinceTs = now - bucket*200`。

## 6. BFF API（统一返回 `{code, msg, data}`）

| 端点 | 说明 | 数据来源 |
|---|---|---|
| `GET /api/health` | BFF 存活 | — |
| `GET /api/overview` | **按池分条**：`level1{ip_count, uptime, total_pulled, pass_rate, duplicate_rate, by_proto, errors, drops, errors_total, api_call_count, avg_remaining}` + `sites[{name, reachable, target_url, base_url, ip_count, free, leased, uptime, total_pulled, pass_rate, avg_latency, by_proto, errors, drops, errors_total, api_call_count, avg_remaining}]` | 内存态 |
| `GET /api/distributions` | **按池分布**：`pools.level1{ttl}`、`pools.<site>{ttl, latency}`；TTL 剩余 11 档（≤1min / 1~3min / 3~5min / 5~10min / 10~30min / 30min~2h / 2h~6h / 6h~12h / 12h~24h / ≥24h / 永久无TTL），已过期归入 ≤1min；延迟 6 档（<200 / 200-500 / 500-1000 / 1000-2000 / 2000-3000 / ≥3000ms） | 内存态 |
| `GET /api/stats` | 累计统计：level1 + 各站点（by_proto/pass_rate/errors）+ 代理层 `{uptime, started_at, total_calls, calls_by_ip, calls_by_site, errors}` | 内存态 |
| `GET /api/sites` | 站点列表（Ips 页筛选用：reachable/total/leased/free/by_proto/avg_latency/pass_rate/errors） | 内存态 |
| `GET /api/ips?protocol=&status=&site=&page=&size=` | 二级池 IP 列表（含 site 列），分页 + 筛选 | 内存态 |
| `GET /api/sites/:site/ips` | 单站点 IP 列表（保留端点，前端暂未使用） | 内存态 |
| `GET /api/sites/:site/status` | 单站点原始 status | 内存态 |
| `POST /api/sites/:site/release-all` | **一键释放**：BFF 代理转发上游 `release-all`，`data` 为释放数量；站点不存在 → `40400`，上游故障 → `50200` | 上游写操作 |
| `GET /api/history?range=1h\|6h\|24h\|7d` | 降采样历史多序列（`series` 按 `level1`/站点名/`global` 分组，每点含 pool_capacity/available_count/avg_latency/pull_rate/pass_rate/duplicate_rate/errors） | metrics 聚合 |

- `status` 筛选值：`free`（空闲）/ `leased`（租赁中）。
- `protocol` 筛选值：`http` / `https` / `socks4` / `socks5`。
- 错误时 `code` 非 0（如 `40400` 站点不存在、`40000` 非法 range、`50200` 上游故障）。

## 7. 前端设计

### 7.1 路由与页面

| 路由 | 组件 | 内容 |
|---|---|---|
| `/` | `Overview.vue` | **按池横条**：一级池 1 条（IP 数/启动时长/总拉取数量/测试通过率/重复率/错误总数/API 调用次数/平均剩余时间）+ 每个二级池 1 条（IP 数/可用 IP/租赁中/测试通过率/平均延迟/错误总数/API 调用次数/平均剩余时间），含在线状态 Tag；无图表 |
| `/ips` | `Ips.vue` | 协议/状态/站点筛选 + 分页 + 延迟着色 + **剩余时间列**；页面自带 5s 自动刷新 |
| `/sites` | `Sites.vue` | Tab = [一级 IP 池 \| 各二级池]；布局：**基础信息横排 → 健康指标横排 → 图表区（1h/6h/24h/7d 切换）**。一级池 5 图：历史池内 IP 数量 / TTL 剩余分布 / 协议扇形 / 历史测试通过率 / 历史重复率；二级池 6 图：另加延迟分布 / 历史平均延迟。二级池右上「一键释放全部 IP」按钮（`el-message-box` 二次确认）。**无 IP 表** |
| `/stats` | `Stats.vue` | 第一行：代理层启动时长 + API 被调用次数；第二行：`calls_by_ip` 表格（按次数降序、可滚动）；第三行：`calls_by_site` 各二级池转发次数横排；统计图（1h/6h/24h/7d，多色折线）：历史池内 IP 数量 / 历史测试通过率 / 拉取速率（一级池+全部二级池各一条线）、历史平均延迟（仅二级池）、历史错误统计（一二级合并 = `global` 序列，每种错误一条线） |

### 7.2 数据层

- `stores/refresh.ts`：全局共享 `intervalSec`（默认 5，`localStorage['netfountain-refresh-interval']` 持久化）。
- `stores/data.ts`：`refreshOnce()` 拉取 `/overview` `/sites` `/distributions` `/stats` 后**触发全部页面钩子**；`start()/restart()/stop()`，轮询周期读 `refresh.intervalSec`；失败时置 `error`（`App.vue` 顶部 `el-alert` 展示，**不白屏，保留上次数据**）。
- 页面钩子：`addHook(fn)` 返回注销函数；站点视图与统计页把 `/api/history` 拉取注册为钩子，使历史图表随每个轮询 tick 一起刷新。
- `api.ts`：`request(path, params, method)` 支持 GET/POST；`code!==0` 或网络异常抛 `ApiError`。
- `Ips.vue`：组件内自带固定 5s 定时刷新（保持剩余时间等字段新鲜）。

### 7.3 自动刷新周期选择器

- `RefreshSelector.vue`（`el-select`，选项 1s/2s/5s/10s/30s/60s，默认 5s）放在总览、站点视图、统计分析三页顶部右侧。
- 三页绑定**同一全局** `refresh` store：改一处全局生效；变更后 `data.restart()` 立即以新周期重启轮询并触发一次即时刷新（不必等满周期）。
- 刷新仅更新响应式数据与图表（computed + `BaseChart` 的 option watch 重渲染），**不重载组件、不刷整页**。

### 7.4 视觉与交互

- **暗色模式**：`stores/app.ts` 维护 `dark`（`localStorage['netfountain-dark']`），切换 `document.documentElement` 的 `dark` class；`main.ts` 已引入 `element-plus/theme-chalk/dark/css-vars.css`；`BaseChart.vue` 监听 `dark` 变化用 ECharts `dark` 主题重新 `init`。
- **延迟着色**（`format.ts` 的 `latencyType`，`el-tag` 渲染）：`<500ms → success(绿)`、`<2000ms → warning(橙)`、`≥2000ms → danger(红)`、`null → info`。
- **剩余时间**：`remainingSeconds(rec) = max(0, created_at + ttl - now)`（**现在到结束**，非拉取时的原始 ttl）；`fmtRemaining` → `永久` / `已过期` / `3m20s` 格式；`fmtDuration` 支持 `1d4h` / `3h24m` / `5m10s`。
- **多色折线**：`format.ts` 的 `PALETTE` 按系列索引取色；错误类型中文标签见 `ERROR_LABELS`。
- **图表**：`charts.ts` 提供 `barChart` / `lineChart` / `stackedBarChart` / `pieChart`，折线图内置 ECharts `dataZoom`（inside + slider）。

## 8. 依赖的 NetFountain 上游契约

BFF 消费以下上游接口（详细字段见根目录 `API_USAGE.md`）：

- **一级池 :8000**：`GET /api/v1/status`（`pool_size/counts/total_pulled/total_entered/total_duplicates/errors/drops/api_call_count/uptime`）、`GET /api/v1/ips`（记录含 `proxy_url/protocol/region/ttl/created_at`，**无延迟、无租赁**）。
- **二级池 :8001+**：`GET /api/v1/status`（`pool_stats{total/by_proto/leased_total/free_total}` + `total_pulled/total_entered/errors/drops/api_call_count/uptime`）、`GET /api/v1/ips`（记录含 `latency_ms/leased/ttl/created_at`）、**写操作** `POST /api/v1/ips/release-all`（仅经 BFF 代理调用）。
- **代理层 :9000**：`GET /api/v1/health`（`sites[]` 站点列表 + `stats{total_calls/calls_by_ip/calls_by_site/errors}` + `uptime/started_at` + `pools` 实时聚合）。

统一信封 `{code, msg, data}`，`code=0` 成功；错误码 `40000/40400/40402/50000/50200`。协议枚举 `http/https/socks4/socks5`。

## 9. 统计指标推导

| 指标 | 公式/来源 |
|---|---|
| 平均剩余时间 | `avg(max(0, created_at + ttl - now))`，仅有限 TTL IP 参与（`ttl=null` 永久项不参与）；BFF `avgRemainingSeconds` 从内存态计算 |
| 拉取速率 (IP/s) | 仅由历史聚合按桶计算：`Δtotal_pulled / 桶宽`（一级池及每站点） |
| 可达性测试通过率 | 累计 `total_entered / total_pulled`；历史按桶 `Δentered / Δpulled`（一级池与站点各自计算） |
| 重复率 | 一级池 `total_duplicates / total_pulled`；历史按桶 |
| 池内 IP 数量 | 一级池 `pool_size`；站点 `total`；可用 `free_total` |
| 平均延迟 | 站点 IP `latency_ms` 平均（跨站点汇总时按站点 IP 数加权） |
| 延迟分布 | 站点 IP `latency_ms` 6 档分桶 |
| TTL 剩余时间 | `created_at + ttl - now`，11 档分桶 + 「永久/无TTL」计数，已过期归入 ≤1min |
| 错误统计 | 每池 `errors_total = Σerrors + drops`（一级池：pull/test/ttl_sweep 失败；站点：sync/test/revalidate/ttl_sweep/empty_acquires）；错误历史取各错误计数桶内 `MAX-MIN`；`global` 序列为一级池+全部站点合并 |

## 10. 关键约束（修改代码务必遵守）

1. **禁止前端直连 8000/8001/9000**，一切数据（含 release-all 写操作）经 BFF `/api/*`。
2. **禁止修改 NetFountain 后端代码**（本子项目只通过其 HTTP API 交互）。
3. **历史接口必须降采样聚合**，禁止把 10 天秒级原始数据返回前端。
4. **采集循环禁止阻塞式长耗时操作**：单次采集 800ms 超时即中断跳过；网络用异步 `fetch`，落库用 `node:sqlite` 同步短写（毫秒级，可接受）。
5. 不引入 WebSocket/SSE、第二套 UI 库、Nginx/Docker/MySQL/Redis。
6. 样式一律用 Element Plus 组件（仅少量 scoped 布局 CSS）。
7. 一级池记录无 `latency_ms`/`leased`：IP 列表页只展示二级池 IP；一级池以总览横条与站点视图 Tab（统计形式）呈现。
8. `release-all` 是**唯一写操作**：必须由 BFF 代理转发（前端禁止直连二级池），且前端必须经 `el-message-box` 二次确认后执行。
