// 急騰記録の手動追加・削除
//   POST   /api/admin/surges  {"ticker","name","date","prev_close","close","keywords"}  前日比は自動計算
//   DELETE /api/admin/surges?ticker=4398.T&date=2026-10-22   手動で追加した記録（source = 'manual'）だけ消せる
//
// 公開画面（surges.json）への反映は、次の同期 → 書き出し → デプロイ（平日 23:50 か手動実行）のとき。
import { db, refreshTicker } from "./db.js";

const TICKER_RE = /^\d{3}[0-9A-Z]\.T$/;     // 4398.T / 130A.T（scripts/sync_notion.py と同じ形）
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const THRESHOLD_PCT = 10;                    // 同期と同じく前日比 +10% 以上だけ受け付ける

const bad = (msg) => Response.json({ error: msg }, { status: 400 });

export async function addSurge(request, env) {
  let body;
  try { body = await request.json(); } catch { return bad("JSON を送ってください"); }
  const ticker = String(body.ticker || "").trim().toUpperCase();
  const name = String(body.name || "").trim();
  const date = String(body.date || "").trim();
  const prev = Number(body.prev_close), close = Number(body.close);
  const keywords = String(body.keywords || "").trim() || null;

  if (!TICKER_RE.test(ticker)) return bad("銘柄コードは 4398.T のように「4 桁（英字可）+ .T」で入力してください");
  if (!name || name.length > 100) return bad("銘柄名を入力してください（100 文字まで）");
  if (!DATE_RE.test(date) || isNaN(Date.parse(date))) return bad("急騰日は YYYY-MM-DD で入力してください");
  if (!(prev > 0) || !(close > 0)) return bad("前日終値と終値は 0 より大きい数値で入力してください");
  const changePct = Math.round((close / prev - 1) * 10000) / 100;
  if (changePct < THRESHOLD_PCT) return bad(`前日比が +${changePct}% です。+${THRESHOLD_PCT}% 以上の急騰だけ登録できます`);

  const sql = db(env);
  const dup = await sql.query("SELECT source FROM surge_events WHERE ticker = $1 AND date = $2", [ticker, date]);
  if (dup.length) return Response.json({ error: `${ticker} の ${date} はすでに登録されています（${dup[0].source}）` }, { status: 409 });

  const now = new Date().toISOString().replace(/\.\d+Z$/, "+00:00");
  await sql.query("INSERT INTO tickers (ticker, name, updated_at) VALUES ($1, $2, $3) ON CONFLICT (ticker) DO NOTHING",
                  [ticker, name, now]);
  await sql.query(
    "INSERT INTO surge_events (ticker, date, prev_close, close, change_pct, source, keywords)"
      + " VALUES ($1, $2, $3, $4, $5, 'manual', $6)",
    [ticker, date, prev, close, changePct, keywords]);
  await refreshTicker(sql, ticker);
  return Response.json({ ok: true, ticker, date, change_pct: changePct }, { status: 201 });
}

export async function deleteSurge(request, env) {
  const q = new URL(request.url).searchParams;
  const ticker = q.get("ticker") || "", date = q.get("date") || "";
  if (!TICKER_RE.test(ticker) || !DATE_RE.test(date)) return bad("ticker と date を指定してください");

  const sql = db(env);
  const rows = await sql.query("SELECT source FROM surge_events WHERE ticker = $1 AND date = $2", [ticker, date]);
  if (!rows.length) return Response.json({ error: "その記録はありません" }, { status: 404 });
  if (rows[0].source !== "manual") {
    return Response.json({ error: "Notion から同期した記録は削除できません（次の同期で戻るため）。Notion 側で削除してください" },
                         { status: 403 });
  }
  await sql.query("DELETE FROM surge_events WHERE ticker = $1 AND date = $2 AND source = 'manual'", [ticker, date]);
  // 急騰記録も月次株価も残っていない銘柄（手動で追加しただけの銘柄）は、銘柄ごと消す
  const removed = await sql.query(
    "DELETE FROM tickers t WHERE ticker = $1"
      + " AND NOT EXISTS (SELECT 1 FROM surge_events e WHERE e.ticker = t.ticker)"
      + " AND NOT EXISTS (SELECT 1 FROM monthly_prices p WHERE p.ticker = t.ticker) RETURNING ticker", [ticker]);
  if (!removed.length) await refreshTicker(sql, ticker);
  return Response.json({ ok: true, ticker_removed: removed.length > 0 });
}
