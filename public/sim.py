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


def simulate_from_price(start_price, initial, monthly, years, annual_rate=DEFAULT_ANNUAL_RATE):
    """急騰日の終値を起点に、株価が年率 annual_rate を維持して上がり続けた場合の推移を返す。

    初月に initial、以降は毎月 monthly をその時点の株価で買い付ける。
    評価額は simulate_fixed と同じになり、加えて各月の想定株価（price）と保有株数（units）を返す。
    """
    monthly_growth = (1 + annual_rate) ** (1 / 12)
    price = float(start_price)
    units = initial / price
    contributed = float(initial)
    rows = [{"month": 0, "price": price, "units": units, "contributed": contributed, "value": units * price}]
    for m in range(1, int(years * 12) + 1):
        price *= monthly_growth
        units += monthly / price
        contributed += monthly
        rows.append({"month": m, "price": price, "units": units, "contributed": contributed, "value": units * price})
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


def run(mode, initial, monthly, years, annual_rate, prices=None, start_price=None):
    """JS から呼ぶエントリポイント。"""
    if mode == "historical" and prices:
        rows = simulate_historical(prices, initial, monthly)
    elif mode == "surge" and start_price:
        rows = simulate_from_price(start_price, initial, monthly, years, annual_rate)
    else:
        rows = simulate_fixed(initial, monthly, years, annual_rate)
    return {"rows": rows, "summary": summarize(rows)}


if __name__ == "__main__":
    s = run("fixed", 0, 30000, 20, DEFAULT_ANNUAL_RATE)["summary"]
    print(f"毎月3万円・年率10%・20年: 元本 {s['contributed']:,.0f} 円 → 評価額 {s['final_value']:,.0f} 円")
    last = run("surge", 0, 30000, 20, DEFAULT_ANNUAL_RATE, start_price=2958)["rows"][-1]
    print(f"急騰日の終値 2,958 円が年率10%を維持: 20年後の株価 {last['price']:,.0f} 円・保有 {last['units']:,.1f} 株")
