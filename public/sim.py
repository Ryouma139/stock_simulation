"""積立・リターンのシミュレーションロジック。

ブラウザ内の Pyodide から呼ばれるほか、ローカルの Python でもそのまま動く
（標準ライブラリのみ使用）。
"""

DEFAULT_ANNUAL_RATE = 0.10  # 想定年率 10%


def simulate_fixed(initial, monthly, years, annual_rate=DEFAULT_ANNUAL_RATE):
    """一定の年率で毎月複利運用した場合の推移を返す。"""
    monthly_rate = (1 + annual_rate) ** (1 / 12) - 1
    value = float(initial)
    contributed = float(initial)
    rows = [{"month": 0, "contributed": contributed, "value": value}]
    for m in range(1, int(years * 12) + 1):
        value = value * (1 + monthly_rate) + monthly
        contributed += monthly
        rows.append({"month": m, "contributed": contributed, "value": value})
    return rows


def simulate_historical(prices, initial, monthly):
    """実際の月末終値で毎月同額を買い付けた（ドルコスト平均法）場合の推移を返す。

    prices: [{"date": "YYYY-MM", "close": float}, ...] 古い順
    """
    units = 0.0
    contributed = 0.0
    rows = []
    for i, p in enumerate(prices):
        buy = monthly + (initial if i == 0 else 0)
        units += buy / p["close"]
        contributed += buy
        rows.append({
            "month": i,
            "date": p["date"],
            "contributed": contributed,
            "value": units * p["close"],
        })
    return rows


def summarize(rows):
    """最終評価額・元本・損益・元本に対する騰落率を返す。"""
    last = rows[-1]
    contributed = last["contributed"]
    return {
        "final_value": last["value"],
        "contributed": contributed,
        "profit": last["value"] - contributed,
        "return_pct": (last["value"] / contributed - 1) * 100 if contributed else 0,
    }


def historical_annual_rate(prices):
    """価格データから実績の年率リターン(CAGR)を計算する。"""
    if len(prices) < 2:
        return 0.0
    years = (len(prices) - 1) / 12
    return (prices[-1]["close"] / prices[0]["close"]) ** (1 / years) - 1


def run(mode, initial, monthly, years, annual_rate, prices=None):
    """JS から呼ぶエントリポイント。"""
    if mode == "historical" and prices:
        rows = simulate_historical(prices, initial, monthly)
    else:
        rows = simulate_fixed(initial, monthly, years, annual_rate)
    return {"rows": rows, "summary": summarize(rows)}


if __name__ == "__main__":
    s = run("fixed", 0, 30000, 20, DEFAULT_ANNUAL_RATE)["summary"]
    print(f"毎月3万円・年率10%・20年: 元本 {s['contributed']:,.0f} 円 → 評価額 {s['final_value']:,.0f} 円")
