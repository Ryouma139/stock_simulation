"""Notion の「株価DB」（急騰記録）を PostgreSQL に同期する（Notion → PostgreSQL の一方向）。

使い方:
    # 環境変数 NOTION_TOKEN（Notion インテグレーションのトークン）と DATABASE_URL（PostgreSQL）が必要
    python scripts/sync_notion.py --init-schema        # 初回：テーブルを作成して直近 7 日ぶんを同期
    python scripts/sync_notion.py --days 30            # 急騰日が直近 30 日の行を同期
    python scripts/sync_notion.py --all                # 全期間を同期
    python scripts/sync_notion.py --sqlite local.db    # PostgreSQL の代わりにローカル SQLite で試す

処理の流れ:
    1. 株価DB から急騰日が期間内の行を取得
    2. 「銘柄」のリンク先（銘柄レポートページ）本文の「ティッカー | 4398.T」からティッカーを特定
       （同期済みの行は DB に保存したティッカーを使い、ページを読み直さない）
    3. 前日比率が +10% 以上の行を surge_events に保存（Notion 側で値が直されたら上書き）
    4. 初登場の銘柄は月次株価を monthly_prices に保存（過去株価モード用）
    5. 全銘柄の hit_count / popularity_score を再計算

Notion 側の準備: インテグレーションを作成し、株価DB と銘柄レポート（stock_reports）のページに接続を追加する。
"""
import argparse
import os
import re
import time
from datetime import date, timedelta

import requests

from db import connect, insert_many
from detect_surges import now_iso, refresh_scores, save_monthly_prices
from popularity import SURGE_THRESHOLD_PCT

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DEFAULT_DATABASE_ID = "1e4e439baae24f2698fbf4b46f9fbe18"  # 📈 株価DB

# 株価DB のプロパティ名
P_NAME, P_DATE, P_RATIO = "銘柄", "急騰日", "前日比率"
P_CLOSE, P_PREV, P_KEYWORDS = "終値（推定）", "前日終値（推定）", "キーワード"

PAGE_ID_RE = re.compile(r"([0-9a-f]{32})(?:[?#]|$)")
TICKER_RE = re.compile(r"(?<![0-9A-Z])(\d{3}[0-9A-Z])(?:\.T)?(?![0-9A-Z])")  # 130A のような英字入りコードも対象
TICKER_STRICT_RE = re.compile(r"(?<![0-9A-Z])\d{3}[0-9A-Z]\.T(?![A-Za-z])")


# ---------- Notion の値の取り出し（ネットワーク不要・テスト対象） ----------

def plain_text(rich):
    return "".join(r.get("plain_text", "") for r in rich or [])


def linked_page_id(rich):
    """タイトル内のページメンション / リンクから、リンク先ページの ID（32 桁）を返す。"""
    for r in rich or []:
        if r.get("type") == "mention" and r["mention"].get("type") == "page":
            return r["mention"]["page"]["id"].replace("-", "")
        url = r.get("href") or ((r.get("text") or {}).get("link") or {}).get("url")
        m = PAGE_ID_RE.search((url or "").replace("-", ""))
        if m:
            return m.group(1)
    return None


def parse_row(page):
    """株価DB の 1 行（Notion API のページ）を dict にする。必要な値が欠けていれば None。"""
    props = page["properties"]
    num = lambda k: (props.get(k) or {}).get("number")
    d = (props.get(P_DATE) or {}).get("date") or {}
    title = (props.get(P_NAME) or {}).get("title")
    close, prev, ratio = num(P_CLOSE), num(P_PREV), num(P_RATIO)
    if ratio is None and close and prev:
        ratio = close / prev - 1
    if not d.get("start") or close is None or ratio is None:
        return None
    report_id = linked_page_id(title)
    return {
        "notion_page_id": page["id"].replace("-", ""),
        "name": plain_text(title).strip(),
        "date": d["start"][:10],
        "close": float(close),
        "prev_close": float(prev) if prev else round(close / (1 + ratio), 4),
        "change_pct": round(ratio * 100, 2),  # Notion は 0.2034 = 20.34% で持っている
        "keywords": plain_text((props.get(P_KEYWORDS) or {}).get("rich_text")),
        "report_page_id": report_id,
        "report_url": f"https://www.notion.so/{report_id}" if report_id else None,
    }


def find_ticker(lines):
    """レポート本文の行から証券コードを探す。「ティッカー」「証券コード」の行を優先する。"""
    for line in lines:
        if "ティッカー" in line or "証券コード" in line:
            m = TICKER_RE.search(line.split("ティッカー")[-1].split("証券コード")[-1])
            if m:
                return f"{m.group(1)}.T"
    for line in lines:  # ラベルが無ければ「4398.T」の形だけを拾う
        m = TICKER_STRICT_RE.search(line)
        if m:
            return m.group(0)
    return None


def block_lines(block):
    """ブロック 1 つ分の表示テキスト。表の行はセルを " | " でつなぐ。"""
    body = block.get(block["type"]) or {}
    if block["type"] == "table_row":
        return [" | ".join(plain_text(c) for c in body.get("cells", []))]
    return [plain_text(body["rich_text"])] if "rich_text" in body else []


# ---------- Notion API ----------

class Notion:
    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
        })

    def request(self, method, path, **kw):
        for _ in range(5):
            res = self.session.request(method, f"{NOTION_API}{path}", timeout=30, **kw)
            if res.status_code == 429:  # レート制限（平均 3 回/秒）
                time.sleep(float(res.headers.get("Retry-After", 1)))
                continue
            if not res.ok:
                raise RuntimeError(f"Notion API {res.status_code}: {res.text[:300]}")
            return res.json()
        raise RuntimeError("Notion API のレート制限が続いたため中断しました")

    def query_database(self, database_id, since=None):
        body = {"page_size": 100, "sorts": [{"property": P_DATE, "direction": "descending"}]}
        if since:
            body["filter"] = {"property": P_DATE, "date": {"on_or_after": since}}
        while True:
            data = self.request("POST", f"/databases/{database_id}/query", json=body)
            yield from data["results"]
            if not data.get("has_more"):
                return
            body["start_cursor"] = data["next_cursor"]

    def page_lines(self, page_id, depth=0):
        """ページ本文をテキスト行で返す（表の中身も含む）。"""
        lines, cursor = [], None
        while True:
            params = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
            data = self.request("GET", f"/blocks/{page_id}/children", params=params)
            for b in data["results"]:
                lines += block_lines(b)
                if b.get("has_children") and b["type"] in ("table", "toggle", "column_list", "column") and depth < 2:
                    lines += self.page_lines(b["id"], depth + 1)
            if not data.get("has_more"):
                return lines
            cursor = data["next_cursor"]


# ---------- DB 保存 ----------

def known_tickers(db, rows):
    """同期済みの行（notion_page_id）→ ティッカー。ページの読み直しを省くために使う。"""
    ids = [r["notion_page_id"] for r in rows]
    found = {}
    for i in range(0, len(ids), 90):
        part = ids[i:i + 90]
        marks = ", ".join("?" * len(part))
        for r in db.query(f"SELECT notion_page_id, ticker FROM surge_events WHERE notion_page_id IN ({marks})", part):
            found[r["notion_page_id"]] = r["ticker"]
    return found


def save_notion_surges(db, rows):
    """ティッカーが決まった行を保存する。戻り値は DB に初登場した銘柄のリスト。"""
    if not rows:
        return []
    tickers = sorted({r["ticker"] for r in rows})
    marks = ", ".join("?" * len(tickers))
    known = {r["ticker"] for r in db.query(f"SELECT ticker FROM tickers WHERE ticker IN ({marks})", tickers)}
    names = {r["ticker"]: r["name"] for r in sorted(rows, key=lambda r: r["date"])}  # 最新の表記を採用

    insert_many(
        db, "INSERT INTO tickers (ticker, name, updated_at) VALUES",
        [(t, names[t], now_iso()) for t in tickers],
        "ON CONFLICT(ticker) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at",
    )
    insert_many(
        db, "INSERT INTO surge_events (ticker, date, prev_close, close, change_pct, source,"
            " notion_page_id, report_url, keywords) VALUES",
        [(r["ticker"], r["date"], r["prev_close"], r["close"], r["change_pct"], "notion",
          r["notion_page_id"], r["report_url"], r["keywords"]) for r in rows],
        "ON CONFLICT(ticker, date) DO UPDATE SET prev_close = excluded.prev_close, close = excluded.close,"
        " change_pct = excluded.change_pct, source = excluded.source, notion_page_id = excluded.notion_page_id,"
        " report_url = excluded.report_url, keywords = excluded.keywords",
    )
    return [t for t in tickers if t not in known]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", help="ローカル SQLite ファイル（省略時は DATABASE_URL の PostgreSQL）")
    parser.add_argument("--init-schema", action="store_true", help="テーブルを作成する")
    parser.add_argument("--database-id", default=os.environ.get("NOTION_DATABASE_ID", DEFAULT_DATABASE_ID))
    parser.add_argument("--days", type=int, default=7, help="急騰日が直近何日の行を同期するか")
    parser.add_argument("--all", action="store_true", help="全期間を同期する")
    parser.add_argument("--threshold", type=float, default=SURGE_THRESHOLD_PCT, help="急騰とみなす前日比（%%）")
    parser.add_argument("--years", type=int, default=20, help="新規銘柄の月次株価を何年ぶん保存するか")
    parser.add_argument("--no-prices", action="store_true", help="月次株価（yfinance）を取得しない")
    args = parser.parse_args()

    if not os.environ.get("NOTION_TOKEN"):
        raise SystemExit("環境変数 NOTION_TOKEN（Notion インテグレーションのトークン）を設定してください")
    notion = Notion(os.environ["NOTION_TOKEN"])
    db = connect(args.sqlite)
    if args.init_schema:
        db.init_schema()

    since = None if args.all else (date.today() - timedelta(days=args.days)).isoformat()
    rows = [r for r in map(parse_row, notion.query_database(args.database_id, since)) if r]
    rows = [r for r in rows if r["change_pct"] >= args.threshold]
    print(f"株価DB から {len(rows)} 行（急騰日 {since or '全期間'} 以降・前日比 +{args.threshold}% 以上）")

    cached = known_tickers(db, rows)
    ready, unresolved = [], []
    for r in rows:
        r["ticker"] = cached.get(r["notion_page_id"])
        if not r["ticker"] and r["report_page_id"]:
            r["ticker"] = find_ticker(notion.page_lines(r["report_page_id"]))
        (ready if r["ticker"] else unresolved).append(r)
    for r in unresolved:
        print(f"  [skip] {r['date']} {r['name']}: レポートページからティッカーを特定できませんでした")

    # 同じ銘柄・同じ急騰日が Notion に 2 行ある場合は後の行で上書き（PRIMARY KEY が重複しないように）
    ready = list({(r["ticker"], r["date"]): r for r in ready}.values())
    new_tickers = save_notion_surges(db, ready)
    for r in sorted(ready, key=lambda r: (r["date"], -r["change_pct"])):
        print(f"  [同期] {r['date']} {r['ticker']} {r['name']} {r['close']:,.0f}円 +{r['change_pct']}%")

    if new_tickers and not args.no_prices:
        from fetch_prices import fetch_monthly

        for t in new_tickers:
            prices = fetch_monthly(t, args.years)
            save_monthly_prices(db, t, prices)
            print(f"  [新規] {t}: 月次株価 {len(prices)} ヶ月分を保存")

    refresh_scores(db)
    print(f"同期 {len(ready)} 件 / 新規銘柄 {len(new_tickers)} 件 / ティッカー不明 {len(unresolved)} 件")


if __name__ == "__main__":
    main()
