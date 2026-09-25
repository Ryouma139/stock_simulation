"""急騰検出・DB 保存・人気スコアのテスト（ネットワーク不要）。

    python -m unittest discover tests
"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from db import SqliteClient, insert_many  # noqa: E402
from detect_surges import find_surges, refresh_scores, save_monthly_prices, save_surges  # noqa: E402
from popularity import popularity_score  # noqa: E402

TODAY = date(2026, 9, 25)


def fresh_db():
    db = SqliteClient(":memory:")
    db.init_schema()
    return db


class FindSurgesTest(unittest.TestCase):
    def test_detects_only_rises_of_10pct_or_more(self):
        closes = {
            "A.T": [("2026-09-22", 100), ("2026-09-24", 110), ("2026-09-25", 115)],  # +10.0%, +4.5%
            "B.T": [("2026-09-24", 100), ("2026-09-25", 109.9)],                     # +9.9%
            "C.T": [("2026-09-24", 100), ("2026-09-25", 80)],                        # 下落
        }
        surges = find_surges(closes)
        self.assertEqual([(s["ticker"], s["date"], s["change_pct"]) for s in surges],
                         [("A.T", "2026-09-24", 10.0)])

    def test_threshold_is_configurable(self):
        closes = {"B.T": [("2026-09-24", 100), ("2026-09-25", 106)]}
        self.assertEqual(len(find_surges(closes, threshold_pct=5)), 1)


class PopularityTest(unittest.TestCase):
    def test_repeat_hits_score_higher(self):
        once = popularity_score([("2026-09-20", 10)], TODAY)
        twice = popularity_score([("2026-09-10", 10), ("2026-09-20", 10)], TODAY)
        self.assertGreater(twice, once)

    def test_recent_and_bigger_rises_weigh_more(self):
        self.assertGreater(popularity_score([("2026-09-25", 10)], TODAY),
                           popularity_score([("2026-08-26", 10)], TODAY))
        self.assertAlmostEqual(popularity_score([("2026-08-26", 10)], TODAY), 0.5, places=3)  # 30 日で半減
        self.assertAlmostEqual(popularity_score([("2026-09-25", 20)], TODAY), 2.0)
        self.assertAlmostEqual(popularity_score([("2026-09-25", 90)], TODAY), 3.0)  # 上限

    def test_old_events_are_ignored(self):
        self.assertEqual(popularity_score([("2025-01-01", 30)], TODAY), 0)


class SaveTest(unittest.TestCase):
    def test_same_company_saved_again_ranks_higher(self):
        db = fresh_db()
        names = {"A.T": "A社", "B.T": "B社"}
        # 1 日目: A と B が同じ +12% で急騰
        day1 = [{"ticker": t, "date": "2026-09-10", "prev_close": 100, "close": 112, "change_pct": 12.0}
                for t in ("A.T", "B.T")]
        self.assertEqual(save_surges(db, day1, names), ["A.T", "B.T"])
        # 2 日目: A だけ再び急騰（＝同じ企業が再保存される）
        day2 = [{"ticker": "A.T", "date": "2026-09-20", "prev_close": 112, "close": 125, "change_pct": 11.6}]
        self.assertEqual(save_surges(db, day2, names), [])  # 既存銘柄なので新規ではない
        refresh_scores(db, TODAY)

        rows = db.query("SELECT ticker, hit_count, popularity_score, last_hit_date FROM tickers"
                        " ORDER BY popularity_score DESC")
        self.assertEqual([r["ticker"] for r in rows], ["A.T", "B.T"])
        self.assertEqual(rows[0]["hit_count"], 2)
        self.assertEqual(rows[0]["last_hit_date"], "2026-09-20")
        self.assertEqual(rows[1]["hit_count"], 1)

    def test_rerun_does_not_double_count(self):
        db = fresh_db()
        surge = [{"ticker": "A.T", "date": "2026-09-10", "prev_close": 100, "close": 112, "change_pct": 12.0}]
        save_surges(db, surge, {})
        save_surges(db, surge, {})
        refresh_scores(db, TODAY)
        self.assertEqual(db.query("SELECT hit_count FROM tickers")[0]["hit_count"], 1)

    def test_insert_many_splits_over_param_limit(self):
        db = fresh_db()
        save_surges(db, [{"ticker": "A.T", "date": "2026-09-10", "prev_close": 1, "close": 2, "change_pct": 100}], {})
        prices = [{"date": f"{2000 + i // 12}-{i % 12 + 1:02d}", "close": float(i)} for i in range(240)]
        save_monthly_prices(db, "A.T", prices)  # 240 行 × 3 列 = 720 バインド → 分割されるはず
        save_monthly_prices(db, "A.T", prices)  # 再実行しても重複しない
        self.assertEqual(db.query("SELECT COUNT(*) AS n FROM monthly_prices")[0]["n"], 240)

    def test_insert_many_noop_on_empty(self):
        insert_many(fresh_db(), "INSERT INTO tickers (ticker, name, updated_at) VALUES", [])


if __name__ == "__main__":
    unittest.main()
