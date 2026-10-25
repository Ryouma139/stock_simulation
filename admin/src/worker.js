// 管理画面の Worker（stock-simulator-admin）の入口。管理画面の HTML（admin/public/）は静的アセットとして配る。
// admin/wrangler.jsonc の run_worker_first で /api/* だけがこのプログラムに届く。
//
//   GET    /api/admin/tables   テーブル一覧と行数
//   GET    /api/admin/rows     テーブルの中身（表示のみ）
//   POST   /api/admin/surges   急騰記録の手動追加
//   DELETE /api/admin/surges   手動で追加した急騰記録の削除
import { authenticate } from "./admin/auth.js";
import { addSurge, deleteSurge } from "./admin/surges.js";
import { listRows } from "./admin/rows.js";
import { listTables } from "./admin/tables.js";

const ROUTES = {
  "/api/admin/tables": { GET: listTables },
  "/api/admin/rows": { GET: listRows },
  "/api/admin/surges": { POST: addSurge, DELETE: deleteSurge },
};

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    const route = ROUTES[pathname.replace(/\/$/, "")];
    if (!route) return Response.json({ error: "not found" }, { status: 404 });
    const handler = route[request.method];
    if (!handler) return Response.json({ error: "method not allowed" }, { status: 405 });

    let user;
    try {
      user = await authenticate(request, env);
    } catch (e) {
      return Response.json({ error: "unauthorized", detail: String(e.message || e) }, { status: 401 });
    }
    try {
      return await handler(request, env, user);
    } catch (e) {
      console.error(e);
      return Response.json({ error: "サーバーでエラーが発生しました: " + (e.message || e) }, { status: 500 });
    }
  },
};
