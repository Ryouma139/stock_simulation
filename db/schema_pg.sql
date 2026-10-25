-- PostgreSQL（Neon / Supabase など）スキーマ
-- 適用: python scripts/sync_notion.py --init-schema  または  psql "$DATABASE_URL" -f db/schema_pg.sql
--
-- テーブル・列は D1 版（schema.sql）と同じにしてあり、detect_surges.py の処理をそのまま使える。
-- 日付は D1 と同じく 'YYYY-MM-DD' の TEXT で持つ（文字列比較で範囲検索できる）。

-- 銘柄マスタ（前日比 +10% 以上を記録した銘柄が登録される）
CREATE TABLE IF NOT EXISTS tickers (
  ticker           TEXT PRIMARY KEY,          -- 例: 4398.T
  name             TEXT NOT NULL,
  hit_count        INTEGER NOT NULL DEFAULT 0,
  popularity_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  first_hit_date   TEXT,
  last_hit_date    TEXT,
  updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickers_popularity ON tickers(popularity_score DESC);

-- 急騰イベント：前日終値比 +10% 以上になった日を 1 行ずつ記録する
CREATE TABLE IF NOT EXISTS surge_events (
  ticker         TEXT NOT NULL REFERENCES tickers(ticker),
  date           TEXT NOT NULL,               -- YYYY-MM-DD（急騰した日）
  prev_close     DOUBLE PRECISION NOT NULL,
  close          DOUBLE PRECISION NOT NULL,
  change_pct     DOUBLE PRECISION NOT NULL,   -- 前日比（%）
  source         TEXT NOT NULL DEFAULT 'yfinance',  -- 'notion'（株価DB から同期）/ 'yfinance'（detect_surges.py）
  notion_page_id TEXT,                         -- 株価DB の行（ページ）ID
  report_url     TEXT,                         -- 銘柄レポートページの URL
  keywords       TEXT,
  PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_surge_date ON surge_events(date DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_surge_notion_page ON surge_events(notion_page_id);

-- 月次終値（シミュレーターの過去株価モードで使う）
CREATE TABLE IF NOT EXISTS monthly_prices (
  ticker TEXT NOT NULL REFERENCES tickers(ticker),
  ym     TEXT NOT NULL,                       -- YYYY-MM
  close  DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (ticker, ym)
);

-- ユーザーが保存したシミュレーション条件
CREATE TABLE IF NOT EXISTS scenarios (
  id          SERIAL PRIMARY KEY,
  title       TEXT NOT NULL,
  mode        TEXT NOT NULL CHECK (mode IN ('fixed', 'historical', 'surge')),
  ticker      TEXT,
  annual_rate DOUBLE PRECISION NOT NULL DEFAULT 0.10,
  initial     INTEGER NOT NULL DEFAULT 0,
  monthly     INTEGER NOT NULL,
  years       INTEGER NOT NULL,
  final_value DOUBLE PRECISION,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_scenarios_created ON scenarios(created_at DESC);
