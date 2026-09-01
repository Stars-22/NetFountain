// SQLite 存储层：node:sqlite（Node 24 内置）单文件数据库。
// 启动开启 WAL，写入走 prepared statement 事务；提供 metrics / ip_snapshots 两张表。

import { DatabaseSync } from 'node:sqlite'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { config } from './config.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// 数据库文件落在 frontend/ 根目录下（与 server/ 同级）
const dbFile = path.isAbsolute(config.dbFile)
  ? config.dbFile
  : path.join(__dirname, '..', config.dbFile)

const db = new DatabaseSync(dbFile)

db.exec('PRAGMA journal_mode = WAL;')
db.exec('PRAGMA synchronous = NORMAL;')

db.exec(`
  CREATE TABLE IF NOT EXISTS metrics (
    ts                    INTEGER NOT NULL,
    site                  TEXT    NOT NULL,
    pool_capacity         INTEGER,
    available_count       INTEGER,
    leased_count          INTEGER,
    avg_latency           REAL,
    min_latency           REAL,
    max_latency           REAL,
    by_proto              TEXT,
    total_pulled          INTEGER,
    total_entered         INTEGER,
    total_duplicates      INTEGER,
    pull_failures         INTEGER,
    test_failures         INTEGER,
    sync_failures         INTEGER,
    revalidate_failures   INTEGER,
    ttl_sweep_failures    INTEGER,
    empty_acquires        INTEGER,
    drops                 INTEGER
  );
`)

db.exec(`
  CREATE TABLE IF NOT EXISTS ip_snapshots (
    ts          INTEGER NOT NULL,
    site        TEXT    NOT NULL,
    proxy_url   TEXT    NOT NULL,
    protocol    TEXT,
    region      TEXT,
    latency_ms  REAL,
    status      TEXT,
    ttl         REAL,
    created_at  REAL
  );
`)

db.exec('CREATE INDEX IF NOT EXISTS idx_metrics_site_ts ON metrics(site, ts);')
db.exec('CREATE INDEX IF NOT EXISTS idx_snapshots_site_ts ON ip_snapshots(site, ts);')

const insertMetric = db.prepare(`
  INSERT INTO metrics (
    ts, site, pool_capacity, available_count, leased_count,
    avg_latency, min_latency, max_latency, by_proto,
    total_pulled, total_entered, total_duplicates,
    pull_failures, test_failures, sync_failures, revalidate_failures,
    ttl_sweep_failures, empty_acquires, drops
  ) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
  )
`)

const insertSnapshot = db.prepare(`
  INSERT INTO ip_snapshots (ts, site, proxy_url, protocol, region, latency_ms, status, ttl, created_at)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
`)

export function saveMetrics(rows) {
  db.exec('BEGIN')
  try {
    for (const r of rows) {
      insertMetric.run(
        r.ts, r.site, r.pool_capacity, r.available_count, r.leased_count,
        r.avg_latency, r.min_latency, r.max_latency, r.by_proto,
        r.total_pulled, r.total_entered, r.total_duplicates,
        r.pull_failures, r.test_failures, r.sync_failures, r.revalidate_failures,
        r.ttl_sweep_failures, r.empty_acquires, r.drops,
      )
    }
    db.exec('COMMIT')
  } catch (e) {
    db.exec('ROLLBACK')
    throw e
  }
}

export function saveSnapshots(rows) {
  db.exec('BEGIN')
  try {
    for (const r of rows) {
      insertSnapshot.run(
        r.ts, r.site, r.proxy_url, r.protocol, r.region,
        r.latency_ms, r.status, r.ttl, r.created_at,
      )
    }
    db.exec('COMMIT')
  } catch (e) {
    db.exec('ROLLBACK')
    throw e
  }
}

// 最近两条指标行（用于估算当前速率）
const latestTwoStmt = db.prepare(
  'SELECT ts, total_pulled FROM metrics WHERE site = ? ORDER BY ts DESC LIMIT 2',
)

export function getLatestRate(site) {
  const rows = latestTwoStmt.all(site)
  if (!rows || rows.length < 2) return null
  const [a, b] = rows
  const dt = a.ts - b.ts
  if (dt <= 0) return null
  const dp = (a.total_pulled || 0) - (b.total_pulled || 0)
  return dp / dt
}

// 按时间范围降采样聚合（禁止返回秒级原始行）
const historyStmt = db.prepare(`
  SELECT
    site,
    CAST(ts / ? AS INTEGER) * ? AS bucket,
    AVG(pool_capacity)                          AS pool_capacity,
    AVG(available_count)                        AS available_count,
    AVG(avg_latency)                            AS avg_latency,
    MAX(total_pulled)     - MIN(total_pulled)   AS pulled_delta,
    MAX(total_entered)    - MIN(total_entered)  AS entered_delta,
    MAX(total_duplicates) - MIN(total_duplicates) AS duplicates_delta,
    MAX(pull_failures)       - MIN(pull_failures)       AS pull_failures,
    MAX(test_failures)       - MIN(test_failures)       AS test_failures,
    MAX(sync_failures)       - MIN(sync_failures)       AS sync_failures,
    MAX(revalidate_failures) - MIN(revalidate_failures) AS revalidate_failures,
    MAX(ttl_sweep_failures)  - MIN(ttl_sweep_failures)  AS ttl_sweep_failures,
    MAX(empty_acquires)      - MIN(empty_acquires)      AS empty_acquires,
    MAX(drops)               - MIN(drops)               AS drops
  FROM metrics
  WHERE ts >= ?
  GROUP BY site, bucket
  ORDER BY bucket ASC
`)

export function queryHistory(bucketSec, sinceTs) {
  const rows = historyStmt.all(bucketSec, bucketSec, sinceTs)
  const series = {}
  for (const row of rows) {
    const site = row.site
    if (!series[site]) series[site] = []
    const pulled = row.pulled_delta || 0
    const entered = row.entered_delta || 0
    series[site].push({
      ts: row.bucket,
      pool_capacity: row.pool_capacity,
      available_count: row.available_count,
      avg_latency: row.avg_latency,
      pull_rate: pulled > 0 ? pulled / bucketSec : 0,
      pass_rate: pulled > 0 ? entered / pulled : null,
      duplicate_rate: pulled > 0 ? (row.duplicates_delta || 0) / pulled : null,
      errors: {
        pull_failures: row.pull_failures || 0,
        test_failures: row.test_failures || 0,
        sync_failures: row.sync_failures || 0,
        revalidate_failures: row.revalidate_failures || 0,
        ttl_sweep_failures: row.ttl_sweep_failures || 0,
        empty_acquires: row.empty_acquires || 0,
        drops: row.drops || 0,
      },
    })
  }
  return series
}

const cleanupMetrics = db.prepare('DELETE FROM metrics WHERE ts < ?')
const cleanupSnapshots = db.prepare('DELETE FROM ip_snapshots WHERE ts < ?')

export function runRetention() {
  const cutoff = Math.floor(Date.now() / 1000) - config.retentionDays * 86400
  const a = cleanupMetrics.run(cutoff).changes
  const b = cleanupSnapshots.run(cutoff).changes
  return a + b
}

export { db }
