"""前日終値比 +10% 以上の銘柄を検出して DB（D1 / SQLite）に保存し、人気スコアを更新する。

使い方:
    # ローカル SQLite で試す
    python scripts/detect_surges.py --sqlite local.db --init-schema

    # 東証の全上場銘柄を対象に D1 へ保存（環境変数 CLOUDFLARE_ACCOUNT_ID / D1_DATABASE_ID / CLOUDFLARE_API_TOKEN）
    python scripts/detect_surges.py --jpx

処理の流れ:
    1. 対象銘柄の直近の日足終値を取得
    2. 前日比 +10% 以上の日を surge_events に保存（同じ銘柄・同じ日は重複保存しない）
    3. 新しく急騰した銘柄の月次終値を monthly_prices に保存（シミュレーター用）
    4. 全銘柄の hit_count と popularity_score を再計算
"""
import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from db import connect, insert_many
from popularity import LOOKBACK_DAYS, SURGE_THRESHOLD_PCT, popularity_score

UNIVERSE_PATH = Path(__file__).resolve().parent.parent / "universe.txt"
JPX_LIST_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"


# ---------- 判定（ネットワーク不要・テスト対象） ----------

def find_surges(daily_closes, threshold_pct=SURGE_THRESHOLD_PCT):
    """daily_closes: {ticker: [("YYYY-MM-DD", close), ...] 古い順}

    連続する 2 営業日の終値を比べ、上昇率が threshold_pct 以上の日をすべて返す。
    取得期間内の全日を判定するので、実行が 1 日抜けても次回で取りこぼしを拾える。
    """
    surges = []
    for ticker, series in daily_closes.items():
        for (_, prev), (d, close) in zip(series, series[1:]):
            if prev and prev > 0:
                pct = (close / prev - 1) * 100
                if pct >= threshold_pct:
                    surges.append({
                        "ticker": ticker, "date": d, "prev_close": prev,
                        "close": close, "change_pct": round(pct, 2),
                    })
    return surges


# ---------- DB 保存 ----------

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_surges(db, surges, names):
    """急騰イベントを保存する。戻り値は DB に初登場した銘柄のリスト。"""
    if not surges:
        return []
    tickers = sorted({s["ticker"] for s in surges})
    marks = ", ".join("?" * len(tickers))
    known = {r["ticker"] for r in db.query(f"SELECT ticker FROM tickers WHERE ticker IN ({marks})", tickers)}

    insert_many(
        db, "INSERT INTO tickers (ticker, name, updated_at) VALUES",
        [(t, names.get(t, t), now_iso()) for t in tickers],
        "ON CONFLICT(ticker) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at",
    )
    insert_many(
        db, "INSERT INTO surge_events (ticker, date, prev_close, close, change_pct) VALUES",
        [(s["ticker"], s["date"], s["prev_close"], s["close"], s["change_pct"]) for s in surges],
        "ON CONFLICT(ticker, date) DO NOTHING",
    )
    return [t for t in tickers if t not in known]


def save_monthly_prices(db, ticker, prices):
    """prices: [{"date": "YYYY-MM", "close": float}, ...]"""
    insert_many(
        db, "INSERT INTO monthly_prices (ticker, ym, close) VALUES",
        [(ticker, p["date"], p["close"]) for p in prices],
        "ON CONFLICT(ticker, ym) DO UPDATE SET close = excluded.close",
    )


def refresh_scores(db, today=None):
    """全銘柄の記録回数・人気スコアを surge_events から計算し直す。

    スコアは経過日数で減衰するため、新しい急騰がない銘柄も毎日更新する。
    """
    today = today or date.today()
    events = {}
    for r in db.query("SELECT ticker, date, change_pct FROM surge_events ORDER BY date"):
        events.setdefault(r["ticker"], []).append((r["date"], r["change_pct"]))

    since = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    for ticker, evs in events.items():
        recent = [e for e in evs if e[0] >= since]
        db.query(
            "UPDATE tickers SET hit_count = ?, popularity_score = ?, first_hit_date = ?,"
            " last_hit_date = ?, updated_at = ? WHERE ticker = ?",
            [len(evs), popularity_score(recent, today), evs[0][0], evs[-1][0], now_iso(), ticker],
        )


# ---------- データ取得（yfinance / JPX） ----------

def load_universe(use_jpx):
    """{ticker: 銘柄名} を返す。"""
    if use_jpx:
        import pandas as pd

        df = pd.read_excel(JPX_LIST_URL, dtype={"コード": str})
        df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]
        return {f"{c}.T": n for c, n in zip(df["コード"], df["銘柄名"])}

    universe = {}
    for line in UNIVERSE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            ticker, _, name = line.partition(",")
            universe[ticker.strip()] = name.strip() or ticker.strip()
    return universe


def download_daily(tickers, days, chunk=200):
    """直近 days 営業日ぶんの日足終値（分割・併合を調整済み）を取得する。"""
    import yfinance as yf

    start = (date.today() - timedelta(days=days * 2 + 7)).isoformat()
    result = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        df = yf.download(part, start=start, interval="1d", auto_adjust=True,
                         group_by="ticker", progress=False, threads=True)
        for t in part:
            try:
                closes = df[t]["Close"].dropna()
            except KeyError:
                continue
            result[t] = [(idx.strftime("%Y-%m-%d"), float(v)) for idx, v in closes.tail(days + 1).items()]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", help="ローカル SQLite ファイル（省略時は D1）")
    parser.add_argument("--init-schema", action="store_true", help="テーブルを作成する")
    parser.add_argument("--jpx", action="store_true", help="東証の全上場銘柄を対象にする（省略時は universe.txt）")
    parser.add_argument("--days", type=int, default=5, help="何営業日さかのぼって判定するか")
    parser.add_argument("--threshold", type=float, default=SURGE_THRESHOLD_PCT, help="急騰とみなす前日比（%%）")
    parser.add_argument("--years", type=int, default=20, help="新規銘柄の月次株価を何年ぶん保存するか")
    args = parser.parse_args()

    db = connect(args.sqlite)
    if args.init_schema:
        db.init_schema()

    universe = load_universe(args.jpx)
    print(f"対象 {len(universe)} 銘柄の日足を取得中…")
    surges = find_surges(download_daily(list(universe), args.days), args.threshold)
    for s in surges:
        print(f"  [急騰] {s['date']} {s['ticker']} {universe.get(s['ticker'], '')} +{s['change_pct']}%")

    new_tickers = save_surges(db, surges, universe)
    if new_tickers:
        from fetch_prices import fetch_monthly

        for t in new_tickers:
            prices = fetch_monthly(t, args.years)
            save_monthly_prices(db, t, prices)
            print(f"  [新規] {t}: 月次株価 {len(prices)} ヶ月分を保存")

    refresh_scores(db)
    top = db.query("SELECT ticker, name, hit_count, popularity_score FROM tickers"
                   " ORDER BY popularity_score DESC LIMIT 10")
    print(f"急騰 {len(surges)} 件 / 新規銘柄 {len(new_tickers)} 件。人気上位:")
    for i, r in enumerate(top, 1):
        print(f"  {i:>2}. {r['name']}（{r['ticker']}） 記録 {r['hit_count']} 回 / スコア {r['popularity_score']:.2f}")


if __name__ == "__main__":
    main()
