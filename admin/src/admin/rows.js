// GET /api/admin/rows?table=surge_events&limit=100&offset=0&ticker=4398.T  テーブルの中身（表示のみ）
import { db, TABLES } from "./db.js";

const MAX_LIMIT = 500;

export async function listRows(request, env) {
  const q = new URL(request.url).searchParams;
  const table = q.get("table");
  if (!TABLES[table]) return Response.json({ error: `table は ${Object.keys(TABLES).join(" / ")} のどれか` }, { status: 400 });
  const limit = Math.min(Math.max(parseInt(q.get("limit") || "100", 10) || 100, 1), MAX_LIMIT);
  const offset = Math.max(parseInt(q.get("offset") || "0", 10) || 0, 0);
  const ticker = (q.get("ticker") || "").trim();

  const sql = db(env);
  // scenarios 以外は ticker 列があるので銘柄で絞り込める
  const where = ticker && table !== "scenarios" ? "WHERE ticker = $1" : "";
  const params = where ? [ticker] : [];
  const [{ n }] = await sql.query(`SELECT count(*)::int AS n FROM ${table} ${where}`, params);
  const rows = await sql.query(
    `SELECT * FROM ${table} ${where} ORDER BY ${TABLES[table].order} LIMIT ${limit} OFFSET ${offset}`, params);
  return Response.json({ table, total: n, limit, offset, rows });
}
