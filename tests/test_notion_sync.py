"""Notion 株価DB → DB 同期・画面用 JSON の書き出し・急騰モードの計算のテスト（ネットワーク不要）。

    python -m unittest discover tests

環境変数 TEST_DATABASE_URL に空の PostgreSQL を指定すると、PostgreSQL での保存も確認する。
"""
import os
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "public"))

from db import SqliteClient  # noqa: E402
from detect_surges import refresh_scores, save_monthly_prices, save_surges  # noqa: E402
from export_snapshot import build_snapshot  # noqa: E402
from sim import run, simulate_fixed, simulate_from_price, surge_lump_sums  # noqa: E402
from sync_notion import block_lines, find_ticker, known_tickers, parse_row, save_notion_surges  # noqa: E402

TODAY = date(2026, 10, 25)
REPORT_ID = "4012e8c9228c819fa54fdfaac315cdcf"


def notion_page(page_id="4022e8c9-228c-8166-8f2d-cdbd9eed5724", name="ブロードバンドセキュリティ",
                day="2026-10-22", close=2958, prev=2457, ratio=0.2034, link=True):
    """Notion API（2022-06-28）が返す株価DB の 1 行と同じ形。"""
    title = {"type": "text", "plain_text": name, "href": None,
             "text": {"content": name, "link": None}}
    if link:
        url = f"https://app.notion.com/p/{REPORT_ID}"
        title["href"] = url
        title["text"]["link"] = {"url": url}
    return {"id": page_id, "properties": {
        "銘柄": {"type": "title", "title": [title]},
        "急騰日": {"type": "date", "date": {"start": day, "end": None}},
        "終値（推定）": {"type": "number", "number": close},
        "前日終値（推定）": {"type": "number", "number": prev},
        "前日比率": {"type": "number", "number": ratio},
        "前日比": {"type": "number", "number": close - prev if close and prev else None},
        "キーワード": {"type": "rich_text", "rich_text": [{"plain_text": "SOC運用, 情報漏えい対策"}]},
        "参照回数": {"type": "number", "number": None},
    }}


def fresh_db():
    db = SqliteClient(":memory:")
    db.init_schema()
    return db


class ParseRowTest(unittest.TestCase):
    def test_reads_values_and_report_link(self):
        r = parse_row(notion_page())
        self.assertEqual(r["name"], "ブロードバンドセキュリティ")
        self.assertEqual(r["date"], "2026-10-22")
        self.assertEqual(r["change_pct"], 20.34)  # Notion の 0.2034 → 20.34%
        self.assertEqual((r["close"], r["prev_close"]), (2958, 2457))
        self.assertEqual(r["report_page_id"], REPORT_ID)
        self.assertEqual(r["notion_page_id"], "4022e8c9228c81668f2dcdbd9eed5724")

    def test_page_mention_is_also_a_link(self):
        page = notion_page(link=False)
        page["properties"]["銘柄"]["title"] = [{"type": "mention", "plain_text": "X社",
                                              "mention": {"type": "page", "page": {"id": "4012e8c9-228c-819f-a54f-dfaac315cdcf"}}}]
        self.assertEqual(parse_row(page)["report_page_id"], REPORT_ID)

    def test_ratio_is_computed_when_missing(self):
        self.assertEqual(parse_row(notion_page(close=110, prev=100, ratio=None))["change_pct"], 10.0)

    def test_incomplete_rows_are_skipped(self):
        self.assertIsNone(parse_row(notion_page(day=None)))
        self.assertIsNone(parse_row(notion_page(close=None, ratio=None)))


class FindTickerTest(unittest.TestCase):
    def test_reads_ticker_row_of_report_table(self):
        row = {"type": "table_row", "table_row": {"cells": [
            [{"plain_text": "ティッカー"}], [{"plain_text": "4398.T（東証スタンダード）"}]]}}
        lines = ["企業名 | 株式会社ブロードバンドセキュリティ", "設立 | 2000年11月", *block_lines(row)]
        self.assertEqual(find_ticker(lines), "4398.T")

    def test_alphanumeric_code_and_missing_suffix(self):
        self.assertEqual(find_ticker(["証券コード: 130A"]), "130A.T")
        self.assertEqual(find_ticker(["ティッカー | 7203"]), "7203.T")

    def test_unlabeled_text_needs_dot_t(self):
        self.assertEqual(find_ticker(["資本金 2000万円", "東証（6758.T）"]), "6758.T")
        self.assertIsNone(find_ticker(["設立 2000年", "従業員 239名"]))


class SaveNotionTest(unittest.TestCase):
    def rows(self, **kw):
        r = parse_row(notion_page(**kw))
        r["ticker"] = "4398.T"
        return r

    def test_resync_updates_values_without_duplicates(self):
        db = fresh_db()
        self.assertEqual(save_notion_surges(db, [self.rows()]), ["4398.T"])
        # Notion 側で終値を直して再同期 → 同じ行が上書きされる
        self.assertEqual(save_notion_surges(db, [self.rows(close=3000, ratio=0.221)]), [])
        rows = db.query("SELECT close, change_pct, source, notion_page_id FROM surge_events")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["close"], rows[0]["change_pct"], rows[0]["source"]), (3000, 22.1, "notion"))
        self.assertEqual(known_tickers(db, [self.rows()]), {"4022e8c9228c81668f2dcdbd9eed5724": "4398.T"})

    def test_notion_and_detector_share_ranking(self):
        db = fresh_db()
        save_notion_surges(db, [self.rows()])
        save_surges(db, [{"ticker": "4398.T", "date": "2026-10-01", "prev_close": 2000, "close": 2300,
                          "change_pct": 15.0}], {})
        refresh_scores(db, TODAY)
        t = db.query("SELECT hit_count, first_hit_date, last_hit_date FROM tickers")[0]
        self.assertEqual((t["hit_count"], t["first_hit_date"], t["last_hit_date"]), (2, "2026-10-01", "2026-10-22"))


class SnapshotTest(unittest.TestCase):
    def test_groups_surges_by_day_newest_first(self):
        db = fresh_db()
        rows = []
        for i, (pid, day, pct) in enumerate([("a" * 32, "2026-10-22", 0.20), ("b" * 32, "2026-10-22", 0.27),
                                             ("c" * 32, "2026-10-21", 0.19)]):
            r = parse_row(notion_page(page_id=pid, day=day, ratio=pct, name=f"社{i}"))
            r["ticker"] = f"100{i}.T"
            rows.append(r)
        save_notion_surges(db, rows)
        save_monthly_prices(db, "1000.T", [{"date": "2026-09", "close": 2000.0}])
        refresh_scores(db, TODAY)

        snap, prices = build_snapshot(db)
        self.assertEqual([d["date"] for d in snap["days"]], ["2026-10-22", "2026-10-21"])
        self.assertEqual([x["ticker"] for x in snap["days"][0]["items"]], ["1001.T", "1000.T"])  # 前日比の大きい順
        files = {t["ticker"]: t["file"] for t in snap["tickers"]}
        self.assertEqual(files["1000.T"], "prices/1000_T.json")
        self.assertIsNone(files["1002.T"])
        self.assertEqual(list(prices), ["1000.T"])


class SurgeSimTest(unittest.TestCase):
    def test_price_keeps_annual_rate(self):
        rows = simulate_from_price(2958, 0, 30000, 20, 0.10)
        self.assertAlmostEqual(rows[12]["price"], 2958 * 1.1)
        self.assertAlmostEqual(rows[-1]["price"], 2958 * 1.1 ** 20)

    def test_value_matches_fixed_rate_mode(self):
        surge = simulate_from_price(2958, 100000, 30000, 20, 0.10)[-1]
        fixed = simulate_fixed(100000, 30000, 20, 0.10)[-1]
        self.assertAlmostEqual(surge["value"], fixed["value"], places=4)
        self.assertAlmostEqual(surge["contributed"], fixed["contributed"])

    def test_lump_sum_one_day_and_hold(self):
        res = surge_lump_sums(1_000_000, 102, 130, 20, 0.10)
        self.assertAlmostEqual(res["one_day"]["profit"], 1_000_000 * (130 / 102 - 1))  # 前日終値で買って急騰日に売る
        self.assertAlmostEqual(res["one_day"]["units"], 1_000_000 / 102)
        self.assertAlmostEqual(res["hold"]["value"], 1_000_000 * 1.1 ** 20)            # 急騰日の終値で買って 20 年
        self.assertAlmostEqual(res["hold"]["return_pct"], (1.1 ** 20 - 1) * 100)
        self.assertEqual(surge_lump_sums(0, 102, 130, 20)["one_day"]["profit"], 0)

    def test_run_dispatches_surge_mode(self):
        res = run("surge", 0, 30000, 1, 0.10, start_price=1000)
        self.assertIn("price", res["rows"][0])
        self.assertNotIn("price", run("fixed", 0, 30000, 1, 0.10)["rows"][0])


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL 未設定")
class PostgresTest(unittest.TestCase):
    def test_sync_and_snapshot_on_postgres(self):
        from db import PostgresClient

        db = PostgresClient(os.environ["TEST_DATABASE_URL"])
        db.query("DROP TABLE IF EXISTS scenarios, monthly_prices, surge_events, tickers")
        db.init_schema()
        r = parse_row(notion_page())
        r["ticker"] = "4398.T"
        self.assertEqual(save_notion_surges(db, [r]), ["4398.T"])
        self.assertEqual(save_notion_surges(db, [r]), [])
        save_monthly_prices(db, "4398.T", [{"date": f"20{i // 12 + 10}-{i % 12 + 1:02d}", "close": 1.0 + i}
                                           for i in range(200)])
        refresh_scores(db, TODAY)
        snap, prices = build_snapshot(db)
        self.assertEqual(snap["days"][0]["items"][0]["change_pct"], 20.34)
        self.assertEqual(snap["tickers"][0]["hit_count"], 1)
        self.assertEqual(len(prices["4398.T"]), 200)


if __name__ == "__main__":
    unittest.main()
