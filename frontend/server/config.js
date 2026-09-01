// 聚合后端 BFF 配置：全部可调项集中在此，环境变量可覆盖。

export const config = {
  // BFF 对外端口（前端只允许经此端口访问）
  port: Number(process.env.BFF_PORT || 3000),

  // NetFountain 三个服务地址（禁止前端直连，仅 BFF 访问）
  level1Url: process.env.LEVEL1_URL || 'http://127.0.0.1:8000',
  proxyUrl: process.env.PROXY_URL || 'http://127.0.0.1:9000',

  // 采集周期：metrics 聚合表写入周期（原计划 1~2s，默认 2s）
  collectIntervalMs: Number(process.env.COLLECT_INTERVAL_MS || 2000),

  // 全量 IP 快照落库周期（低频，避免秒级全量写放大）
  snapshotIntervalMs: Number(process.env.SNAPSHOT_INTERVAL_MS || 30000),

  // 单次采集超时：超过即中断跳过，不阻塞采集循环
  fetchTimeoutMs: Number(process.env.FETCH_TIMEOUT_MS || 800),

  // 数据保留天数，每天 0 点清理
  retentionDays: Number(process.env.RETENTION_DAYS || 10),

  // SQLite 单文件数据库
  dbFile: process.env.DB_FILE || 'netfountain.db',
}
