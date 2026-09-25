"""D1 の銘柄・株価・シナリオを返す Python Worker API（雛形）。

  GET  /api/tickers?limit=50   急騰記録のある銘柄を人気スコア順に返す
  GET  /api/prices?ticker=XXX  月次終値（シミュレーターの過去株価モード用）
  GET  /api/scenarios   最新 50 件
  POST /api/scenarios   {"title","mode","ticker","annual_rate","initial","monthly","years","final_value"}

Python Workers はまだ仕様変更が多いので、デプロイ前に公式ドキュメントで API を確認すること。
"""
import json
from urllib.parse import parse_qs, urlparse

from workers import Response, WorkerEntrypoint

HEADERS = {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",  # 本番は Pages のドメインに絞る
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "content-type",
}


def json_response(data, status=200):
    return Response(json.dumps(data, ensure_ascii=False), status=status, headers=HEADERS)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlparse(request.url)
        path, qs = url.path, parse_qs(url.query)
        if request.method == "OPTIONS":
            return Response("", headers=HEADERS)
        if path == "/api/tickers" and request.method == "GET":
            return await self.tickers(qs)
        if path == "/api/prices" and request.method == "GET":
            return await self.prices(qs)
        if path != "/api/scenarios":
            return json_response({"error": "not found"}, 404)

        if request.method == "GET":
            result = await self.env.DB.prepare(
                "SELECT * FROM scenarios ORDER BY created_at DESC LIMIT 50"
            ).all()
            return json_response(result.results.to_py())

        if request.method == "POST":
            body = json.loads(await request.text())
            if body.get("mode") not in ("fixed", "historical") or not body.get("title"):
                return json_response({"error": "invalid input"}, 400)
            await self.env.DB.prepare(
                "INSERT INTO scenarios (title, mode, ticker, annual_rate, initial, monthly, years, final_value)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            ).bind(
                str(body["title"])[:100],
                body["mode"],
                body.get("ticker"),
                float(body.get("annual_rate", 0.10)),
                int(body.get("initial", 0)),
                int(body.get("monthly", 0)),
                int(body.get("years", 1)),
                body.get("final_value"),
            ).run()
            return json_response({"ok": True}, 201)

        return json_response({"error": "method not allowed"}, 405)

    async def tickers(self, qs):
        limit = min(max(int(qs.get("limit", ["50"])[0] or 50), 1), 200)
        result = await self.env.DB.prepare(
            "SELECT t.ticker, t.name, t.hit_count, t.popularity_score, t.last_hit_date,"
            " (SELECT change_pct FROM surge_events e WHERE e.ticker = t.ticker"
            "  ORDER BY e.date DESC LIMIT 1) AS last_change_pct"
            " FROM tickers t"
            " WHERE EXISTS (SELECT 1 FROM monthly_prices p WHERE p.ticker = t.ticker)"
            " ORDER BY t.popularity_score DESC, t.last_hit_date DESC LIMIT ?"
        ).bind(limit).all()
        return json_response(result.results.to_py())

    async def prices(self, qs):
        ticker = qs.get("ticker", [""])[0]
        if not ticker:
            return json_response({"error": "ticker is required"}, 400)
        found = (await self.env.DB.prepare(
            "SELECT ticker, name FROM tickers WHERE ticker = ?"
        ).bind(ticker).all()).results.to_py()
        if not found:
            return json_response({"error": "unknown ticker"}, 404)
        result = await self.env.DB.prepare(
            "SELECT ym AS date, close FROM monthly_prices WHERE ticker = ? ORDER BY ym"
        ).bind(ticker).all()
        info = found[0]
        return json_response({"ticker": info["ticker"], "name": info["name"],
                              "prices": result.results.to_py()})
