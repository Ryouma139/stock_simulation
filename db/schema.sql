-- Cloudflare D1 (SQLite) スキーマ
-- 適用: npx wrangler d1 execute stock-sim --remote --file=db/schema.sql

-- 銘柄マスタ（前日比 +10% 以上を記録した銘柄が登録される）
CREATE TABLE IF NOT EXISTS tickers (
  ticker           TEXT PRIMARY KEY,          -- 例: 7203.T
  name             TEXT NOT NULL,
  hit_count        INTEGER NOT NULL DEFAULT 0, -- 急騰の記録回数（同じ企業が保存されるたびに +1）
  popularity_score REAL NOT NULL DEFAULT 0,    -- 重み付き人気スコア（scripts/popularity.py で計算）
  first_hit_date   TEXT,
  last_hit_date    TEXT,
  updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickers_popularity ON tickers(popularity_score DESC);

-- 急騰イベント：前日終値比 +10% 以上になった日を 1 行ずつ記録する
CREATE TABLE IF NOT EXISTS surge_events (
  ticker     TEXT NOT NULL REFERENCES tickers(ticker),
  date       TEXT NOT NULL,                 -- YYYY-MM-DD（急騰した日）
  prev_close REAL NOT NULL,
  close      REAL NOT NULL,
  change_pct REAL NOT NULL,                 -- 前日比（%）
  PRIMARY KEY (ticker, date)                -- 同じ日を二重に記録しない
);
CREATE INDEX IF NOT EXISTS idx_surge_date ON surge_events(date DESC);

-- 月次終値（シミュレーターの過去株価モードで使う）
CREATE TABLE IF NOT EXISTS monthly_prices (
  ticker TEXT NOT NULL REFERENCES tickers(ticker),
  ym     TEXT NOT NULL,                     -- YYYY-MM
  close  REAL NOT NULL,
  PRIMARY KEY (ticker, ym)
);

-- ユーザーが保存したシミュレーション条件
CREATE TABLE IF NOT EXISTS scenarios (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  title       TEXT NOT NULL,
  mode        TEXT NOT NULL CHECK (mode IN ('fixed', 'historical')),
  ticker      TEXT,
  annual_rate REAL NOT NULL DEFAULT 0.10,   -- 想定年率 10%
  initial     INTEGER NOT NULL DEFAULT 0,
  monthly     INTEGER NOT NULL,
  years       INTEGER NOT NULL,
  final_value REAL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scenarios_created ON scenarios(created_at DESC);
