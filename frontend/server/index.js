// NetFountain 前端面板 + 数据聚合后端 BFF 启动入口（Express 5）。
// - 开发：npm run dev 由 concurrently 同时起本服务(3000)与 Vite(5173)
// - 生产：npm run build 后本服务托管 dist/ 并提供 /api，npm start 单命令启动
//
// 模块划分：collector（采集）/ aggregators（聚合）/ routes（API 装配）
//          / scheduler（定时清理）/ db（存储）/ buckets（分桶常量表）/ util（工具）。

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import express from 'express'

import { config } from './config.js'
import { db } from './db.js'
import { startCollector } from './collector/index.js'
import { registerRoutes } from './routes/index.js'
import { scheduleRetention } from './scheduler.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const distDir = path.join(__dirname, '..', 'dist')

// ---- Express 装配 ----
const app = express()
registerRoutes(app)

// ---- 生产静态托管 + SPA fallback ----
if (fs.existsSync(distDir)) {
  app.use(express.static(distDir))
  app.use((req, res, next) => {
    if (req.method !== 'GET' || req.path.startsWith('/api')) return next()
    res.sendFile(path.join(distDir, 'index.html'))
  })
}

// ---- 启动 ----
startCollector()
scheduleRetention()

app.listen(config.port, () => {
  console.log(`[bff] NetFountain BFF listening on http://localhost:${config.port}`)
  console.log(`[bff] level1=${config.level1Url} proxy=${config.proxyUrl}`)
  console.log(`[bff] db=${config.dbFile} collect=${config.collectIntervalMs}ms snapshot=${config.snapshotIntervalMs}ms`)
})

// 进程退出时优雅关闭
function shutdown() {
  try {
    db.close()
  } catch {}
  process.exit(0)
}
process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
