// 管理画面 API 共通：Neon への接続と、表示を許すテーブルの定義。
import { neon } from "@neondatabase/serverless";

export function db(env) {
  if (!env.DATABASE_URL) throw new Error("DATABASE_URL が設定されていません");
  return neon(env.DATABASE_URL);
}

// 管理画面で一覧表示できるテーブル（ここに無いテーブル名は受け付けない）と並び順
export const TABLES = {
  surge_events: { label: "急騰記録", order: "date DESC, change_pct DESC" },
  tickers: { label: "銘柄", order: "last_hit_date DESC NULLS LAST, popularity_score DESC" },
  monthly_prices: { label: "月次株価", order: "ticker, ym DESC" },
  scenarios: { label: "シミュレーション条件", order: "created_at DESC" },
};

// 人気スコアの計算（scripts/popularity.py と同じ式・定数。変えるときは両方を揃える）
const SURGE_THRESHOLD_PCT = 10, HALF_LIFE_DAYS = 30, MAX_MAGNITUDE = 3, LOOKBACK_DAYS = 365;

function popularityScore(events, today) {
  let total = 0;
  for (const e of events) {
    const age = Math.round((today - new Date(e.date + "T00:00:00Z")) / 86400_000);
    if (age < 0 || age > LOOKBACK_DAYS) continue;
    total += Math.min(e.change_pct / SURGE_THRESHOLD_PCT, MAX_MAGNITUDE) * 0.5 ** (age / HALF_LIFE_DAYS);
  }
  return Math.round(total * 10000) / 10000;
}

// 1 銘柄の急騰回数・人気スコア・最初/最後の急騰日を surge_events から計算し直す
// （scripts/detect_surges.py の refresh_scores を 1 銘柄ぶんだけ行う）
export async function refreshTicker(sql, ticker) {
  const events = await sql.query("SELECT date, change_pct FROM surge_events WHERE ticker = $1 ORDER BY date", [ticker]);
  const now = new Date();
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  await sql.query(
    "UPDATE tickers SET hit_count = $1, popularity_score = $2, first_hit_date = $3, last_hit_date = $4, updated_at = $5"
      + " WHERE ticker = $6",
    [events.length, popularityScore(events, today), events[0]?.date ?? null, events.at(-1)?.date ?? null,
     now.toISOString().replace(/\.\d+Z$/, "+00:00"), ticker],
  );
}
