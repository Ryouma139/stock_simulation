"""急騰イベントから銘柄の人気スコアを計算する（重み付け処理）。

同じ企業が何度も急騰リストに保存されるほどスコアが積み上がり、人気銘柄として上位に表示される。
1 回の急騰の重み = 上昇幅の重み × 経過日数による減衰

- 上昇幅の重み : change_pct / 10（+10% で 1.0、+20% で 2.0。上限は MAX_MAGNITUDE）
- 経過日数の減衰: 0.5 ** (経過日数 / HALF_LIFE_DAYS)（30 日前の急騰は重みが半分になる）

スコア = 全イベントの重みの合計。最近・何度も・大きく上がった銘柄ほど高くなる。
"""
from datetime import date

SURGE_THRESHOLD_PCT = 10.0  # 前日比 +10% 以上を急騰とみなす
HALF_LIFE_DAYS = 30
MAX_MAGNITUDE = 3.0         # ストップ高の連続などで 1 回の重みが大きくなりすぎないようにする
LOOKBACK_DAYS = 365         # これより古いイベントはスコアに含めない


def event_weight(change_pct, event_date, today):
    age = (today - event_date).days
    if age < 0 or age > LOOKBACK_DAYS:
        return 0.0
    magnitude = min(change_pct / SURGE_THRESHOLD_PCT, MAX_MAGNITUDE)
    return magnitude * 0.5 ** (age / HALF_LIFE_DAYS)


def popularity_score(events, today=None):
    """events: [(date or "YYYY-MM-DD", change_pct), ...]"""
    today = today or date.today()
    total = 0.0
    for d, pct in events:
        if isinstance(d, str):
            d = date.fromisoformat(d)
        total += event_weight(pct, d, today)
    return round(total, 4)
