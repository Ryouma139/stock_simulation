// GET /api/admin/tables  管理画面で見られるテーブルと行数
import { db, TABLES } from "./db.js";

export async function listTables(request, env, user) {
  const sql = db(env);
  const tables = [];
  for (const [name, t] of Object.entries(TABLES)) {
    const [{ n }] = await sql.query(`SELECT count(*)::int AS n FROM ${name}`);  // name は TABLES のキーのみ
    tables.push({ name, label: t.label, rows: n });
  }
  return Response.json({ user, tables });
}
