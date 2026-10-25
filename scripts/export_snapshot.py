"""DB（PostgreSQL / D1 / SQLite）の急騰記録と月次株価を、画面用の静的 JSON に書き出す。

使い方:
    python scripts/export_snapshot.py                    # DATABASE_URL の PostgreSQL から public/data/ へ
    python scripts/export_snapshot.py --sqlite local.db  # ローカル SQLite から

出力:
    public/data/surges.json          急騰日ごとの銘柄一覧（直近 --days 日ぶん）と人気ランキング
    public/data/prices/<ticker>.json 月次終値（過去株価モード用。保存済みの銘柄のみ）

Cloudflare Pages は静的ホスティングなので、同期 → 書き出し → デプロイ の順に実行して画面を更新する。
"""
import argparse
import json
from pathlib import Path

from db import connect
from detect_surges import now_iso

OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "data"


def price_file(ticker):
    return "prices/" + ticker.replace("^", "").replace(".", "_") + ".json"


def build_snapshot(db, days=30, limit=200):
    """画面が読む surges.json の中身を作る。戻り値は (snapshot, {ticker: 月次株価})。"""
    dates = [r["date"] for r in db.query(
        "SELECT DISTINCT date FROM surge_events ORDER BY date DESC LIMIT ?", [days])]
    by_date = {d: [] for d in dates}
    if dates:
        marks = ", ".join("?" * len(dates))
        for r in db.query(
            "SELECT e.ticker, t.name, e.date, e.prev_close, e.close, e.change_pct, e.keywords, e.report_url"
            f" FROM surge_events e JOIN tickers t ON t.ticker = e.ticker WHERE e.date IN ({marks})"
            " ORDER BY e.date DESC, e.change_pct DESC", dates,
        ):
            by_date[r.pop("date")].append(r)

    tickers = db.query(
        "SELECT t.ticker, t.name, t.hit_count, t.popularity_score, t.last_hit_date,"
        " (SELECT change_pct FROM surge_events e WHERE e.ticker = t.ticker"
        "  ORDER BY e.date DESC LIMIT 1) AS last_change_pct"
        " FROM tickers t ORDER BY t.popularity_score DESC, t.last_hit_date DESC LIMIT ?", [limit])

    prices = {}
    for t in tickers:
        rows = db.query("SELECT ym AS date, close FROM monthly_prices WHERE ticker = ? ORDER BY ym", [t["ticker"]])
        t["file"] = price_file(t["ticker"]) if rows else None
        if rows:
            prices[t["ticker"]] = rows

    snapshot = {
        "updated_at": now_iso(),
        "days": [{"date": d, "items": by_date[d]} for d in dates],
        "tickers": tickers,
    }
    return snapshot, prices


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", help="ローカル SQLite ファイル（省略時は DATABASE_URL の PostgreSQL）")
    parser.add_argument("--days", type=int, default=30, help="急騰日を直近何日ぶん書き出すか")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    snapshot, prices = build_snapshot(connect(args.sqlite), args.days)
    (args.out / "prices").mkdir(parents=True, exist_ok=True)
    names = {t["ticker"]: t["name"] for t in snapshot["tickers"]}
    for ticker, rows in prices.items():
        (args.out / price_file(ticker)).write_text(json.dumps(
            {"ticker": ticker, "name": names[ticker], "updated_at": snapshot["updated_at"], "prices": rows},
            ensure_ascii=False), encoding="utf-8")
    (args.out / "surges.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    n = sum(len(d["items"]) for d in snapshot["days"])
    print(f"急騰 {len(snapshot['days'])} 日・{n} 件 / 銘柄 {len(snapshot['tickers'])} 件 / 月次株価 {len(prices)} 銘柄"
          f" → {args.out}")


if __name__ == "__main__":
    main()
