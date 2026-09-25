"""銘柄の月次終値を取得して public/data/<ticker>.json に保存する。

使い方:
    pip install yfinance
    python scripts/fetch_prices.py 7203.T 6758.T ^N225 --years 20

GitHub Actions で定期実行し、Cloudflare Pages に一緒にデプロイする想定。
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "data"


def fetch_monthly(ticker, years):
    hist = yf.Ticker(ticker).history(period=f"{years}y", interval="1mo", auto_adjust=True)
    hist = hist.dropna(subset=["Close"])
    return [
        {"date": idx.strftime("%Y-%m"), "close": round(float(row["Close"]), 4)}
        for idx, row in hist.iterrows()
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--years", type=int, default=20)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index_path = OUT_DIR / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []

    for ticker in args.tickers:
        prices = fetch_monthly(ticker, args.years)
        if not prices:
            print(f"[skip] {ticker}: データなし")
            continue
        name = yf.Ticker(ticker).info.get("shortName", ticker)
        fname = ticker.replace("^", "").replace(".", "_") + ".json"
        (OUT_DIR / fname).write_text(
            json.dumps({
                "ticker": ticker,
                "name": name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "prices": prices,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        index = [e for e in index if e["ticker"] != ticker]
        index.append({"ticker": ticker, "name": name, "file": fname})
        print(f"[ok] {ticker} ({name}): {len(prices)} ヶ月分")

    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
