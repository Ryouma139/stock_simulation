# 株価シミュレーター：Notion → PostgreSQL 同期ガイド

Notion の「📈 株価DB」に記録した **前日比 +10% 以上の急騰銘柄** を PostgreSQL（Neon）に同期し、
その日の急騰銘柄の確認と「急騰日の終値から株価が年率 10% を維持した場合」の積立シミュレーションを行う。

最終更新: 2026-10-25

---

## 1. シミュレーターの概要

計算はブラウザ内の Python（Pyodide）で行う。ロジックは [public/sim.py](../public/sim.py)、画面は [public/index.html](../public/index.html)。

| モード | 内容 | 使うデータ |
|---|---|---|
| **急騰銘柄が年率を維持**（初期表示） | 急騰日の終値を起点に、株価が想定年率（初期値 10%）で上がり続けた場合。毎月同額を買い付ける | `surge_events` の終値 |
| **想定年率で計算** | 一定の年率で毎月複利運用した場合 | なし |
| **銘柄の過去株価で計算** | 実際の月末終値で毎月同額を買い付けた場合（ドルコスト平均法） | `monthly_prices` |

画面の構成（上から）:

1. **前日比 +10% 以上の急騰銘柄**：急騰日を選ぶと、その日の銘柄・終値・前日比・前日終値・キーワードを表示。行をクリックすると「年率を維持」モードで計算する。銘柄名から Notion の銘柄レポートを開ける
2. **条件**：モード・銘柄・想定年率・初期投資額・毎月の積立額・期間
3. **結果**：最終評価額・元本合計・損益・騰落率、資産推移グラフ、年ごとの表（年率維持モードでは想定株価と保有株数も表示）
4. **人気銘柄ランキング**：何度も・最近・大きく急騰した銘柄ほど上位。クリックすると過去株価モードで計算する

### 「年率 10% を維持」の計算について

- 株価 = 急騰日の終値 × (1 + 年率) ^ (経過年数)。毎月その時点の株価で積立額ぶんを買う
- **評価額は積立額と年率だけで決まるため、どの銘柄でも同じになる**（例: 毎月 3 万円・20 年 → 21,547,777 円）
- 銘柄ごとに変わるのは **起点の株価・N 年後の想定株価・保有株数**
  - 例: GMOプライム・ストラテジー（5250.T）終値 1,399 円 → 20 年後 9,412 円、保有 2,289.4 株
- 株価が一定の年率で上がり続けるという仮定の計算で、急騰後の値動きを予測するものではない

---

## 2. 全体の流れ

```
[Notion]
  Claude Contens
  ├─ 📈 株価DB            急騰日・終値・前日終値・前日比率・キーワード
  └─ stock_reports/<日付>/<日付>_<銘柄>   銘柄レポート（ティッカーを読む）
        │ Notion API（読み取りのみ）
        ▼
  scripts/sync_notion.py ──── Yahoo Finance（初登場の銘柄の月次株価 20 年分）
        │ DATABASE_URL
        ▼
[PostgreSQL（Neon）]  tickers / surge_events / monthly_prices / scenarios
        │ scripts/export_snapshot.py
        ▼
  public/data/surges.json + public/data/prices/*.json
        │ （ローカル確認 or Cloudflare Pages にデプロイ）
        ▼
[画面] public/index.html + sim.py
```

同期は **Notion → PostgreSQL の一方向**。Notion には書き込まない。

---

## 3. 取得元（Notion のどこから読むか）

### ① 📈 株価DB（`Claude Contens` 直下のデータベース）

1 行 = 1 銘柄の 1 回の急騰。読むプロパティ:

| Notion のプロパティ | 型 | 例 | 保存先 |
|---|---|---|---|
| 銘柄 | タイトル（レポートページへのリンク付き） | ブロードバンドセキュリティ | `tickers.name`、リンク先 → `surge_events.report_url` |
| 急騰日 | 日付 | 2026-10-22 | `surge_events.date` |
| 終値（推定） | 数値（円） | 2958 | `surge_events.close` |
| 前日終値（推定） | 数値（円） | 2457 | `surge_events.prev_close` |
| 前日比率 | 数値（%表示。中身は小数） | 0.2034 | `surge_events.change_pct`（×100 → 20.34） |
| キーワード | テキスト | SOC運用, 情報漏えい対策, … | `surge_events.keywords` |

使っていないプロパティ: **前日比**（円。終値 − 前日終値で計算できる）、**参照回数**。

### ② 銘柄レポート（`Claude Contens / stock_reports / <日付> / <日付>_<銘柄>`）

株価DB にはティッカー（証券コード）の列がないため、「銘柄」のリンク先のレポートページを開き、
「基本情報」の表から次の行を読む。

```
| ティッカー | 4398.T（東証スタンダード） |
```

- 「ティッカー」または「証券コード」の行から `1234` / `1234.T` / `130A`（英字入りコード）を拾い、`.T` 付きにする
- 一度同期した行はティッカーを DB に保存しているので、2 回目以降はレポートページを読み直さない
- ティッカーが見つからない行はスキップして `[skip]` と表示する

---

## 4. 初回セットアップ

### 4-1. Notion インテグレーション

1. https://www.notion.so/profile/integrations で新しいインテグレーションを作成
   - 種類: 内部（Internal）／関連ワークスペース: 株価DB のあるワークスペース
   - 機能: **コンテンツを読み取る** のみ
2. 「内部インテグレーションシークレット」（`ntn_…`）をコピー → `NOTION_TOKEN`
3. ページに接続を追加（どちらかの方法）
   - **方法A（推奨）**: インテグレーション設定の「アクセス」→「アクセス権限を編集」→ **Claude Contens** を選択
   - **方法B**: Notion で Claude Contens を開き「•••」→「接続」→ インテグレーション名を検索して追加
   - 親ページ（Claude Contens）に付ければ、株価DB と stock_reports の両方が読める

現在のインテグレーション名: **kabukaDB**

### 4-2. Neon（PostgreSQL）

1. https://neon.com で登録（Free プランで十分。容量 0.5GB に対し使用量は数十 MB 程度）
2. プロジェクトを作成（Region は日本に近いもの。現在は `ap-southeast-2`（シドニー）、PostgreSQL 18）
3. ダッシュボードの「Connect」から接続 URL をコピー → `DATABASE_URL`

### 4-3. `.env` に書く

プロジェクト直下の `.env`（Git の管理外）に書く。見本は [.env.example](../.env.example)。

```
NOTION_TOKEN=ntn_xxxxxxxx
DATABASE_URL=postgresql://neondb_owner:xxxx@ep-xxxx-pooler.ap-southeast-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

- `.env` はスクリプト起動時に自動で読み込まれる（[scripts/db.py](../scripts/db.py) の `load_dotenv`）。PowerShell の `$env:` で設定した値があればそちらが優先
- **トークンや接続 URL は `.env.example` に書かない**（`.env.example` はコミットされる）

### 4-4. Python 環境

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

---

## 5. 同期手順

### 初回（テーブル作成＋全期間）

```powershell
.venv\Scripts\python scripts\sync_notion.py --init-schema --all
.venv\Scripts\python scripts\export_snapshot.py
```

### 2 回目以降（株価DB に行が増えたとき）

```powershell
.venv\Scripts\python scripts\sync_notion.py        # 急騰日が直近 7 日の行を同期
.venv\Scripts\python scripts\export_snapshot.py    # 画面用 JSON を書き出す
```

### 画面で確認

```powershell
cd public
python -m http.server 8000      # http://localhost:8000
```

### `sync_notion.py` のオプション

| オプション | 内容 | 既定 |
|---|---|---|
| `--init-schema` | テーブルを作成する（既にあれば何もしない） | なし |
| `--days N` | 急騰日が直近 N 日の行を同期 | 7 |
| `--all` | 全期間を同期 | なし |
| `--threshold P` | 急騰とみなす前日比（%） | 10 |
| `--years N` | 新規銘柄の月次株価を何年ぶん取るか | 20 |
| `--no-prices` | 月次株価（Yahoo Finance）を取得しない | なし |
| `--sqlite FILE` | PostgreSQL の代わりにローカル SQLite に保存 | なし |

### 同期の処理内容

1. 株価DB から、急騰日が期間内の行を取得
2. 前日比率が +10% 以上の行だけ残す
3. レポートページからティッカーを特定（同期済みの行は DB の値を使う）
4. `surge_events` に保存。同じ銘柄・同じ日は 1 行で、Notion 側で値を直したら上書き
5. 初めて出てきた銘柄は Yahoo Finance から月次株価を取得して `monthly_prices` に保存
6. 全銘柄の急騰回数・人気スコアを再計算

### うまくいかないとき

| 表示 | 原因 | 対処 |
|---|---|---|
| `環境変数 NOTION_TOKEN … を設定してください` | `.env` にトークンがない | `.env` を確認 |
| `Notion API 401` | トークンが間違っている | コピーし直す |
| `Notion API 404 object_not_found` | 株価DB にインテグレーションの接続がない | 4-1 の 3. をやり直す |
| `[skip] … ティッカーを特定できませんでした` | レポートが読めない／「ティッカー」の行がない | stock_reports に接続を付ける／レポートに行を足す |
| `… No data found, symbol may be delisted` | Yahoo Finance にその銘柄の株価がない | 月次株価が 0 件になるだけで同期は続く |

---

## 6. テーブル

スキーマ: [db/schema_pg.sql](../db/schema_pg.sql)（PostgreSQL）／[db/schema.sql](../db/schema.sql)（D1・SQLite。列は同じ）。
日付は `YYYY-MM-DD`、年月は `YYYY-MM` の文字列で持つ。

### 6-1. `surge_events`（急騰の記録）

株価DB の 1 行 = 1 行。主キーは `(ticker, date)`。

| 列 | 型 | 意味 | 取得元 |
|---|---|---|---|
| `ticker` | TEXT | 銘柄コード（例 `4398.T`） | ② レポートの「ティッカー」行 |
| `date` | TEXT | 急騰した日 | ① 急騰日 |
| `prev_close` | DOUBLE | 前日の終値（円） | ① 前日終値（推定） |
| `close` | DOUBLE | 急騰日の終値（円） | ① 終値（推定） |
| `change_pct` | DOUBLE | 前日比（%） | ① 前日比率 × 100 |
| `source` | TEXT | データの出どころ | `notion`（自動検出スクリプトの分は `yfinance`） |
| `notion_page_id` | TEXT | 株価DB の行 ID（一意） | ① 行の ID |
| `report_url` | TEXT | 銘柄レポートの URL | ① 銘柄のリンク先 |
| `keywords` | TEXT | 事業キーワード | ① キーワード |

### 6-2. `tickers`（銘柄一覧・人気スコア）

銘柄ごとに 1 行。主キーは `ticker`。

| 列 | 型 | 意味 | 取得元 |
|---|---|---|---|
| `ticker` | TEXT | 銘柄コード | ② レポートの「ティッカー」行 |
| `name` | TEXT | 銘柄名 | ① 銘柄（タイトルの文字） |
| `hit_count` | INTEGER | 急騰した回数 | `surge_events` から計算 |
| `popularity_score` | DOUBLE | 人気スコア | `surge_events` から計算（下記） |
| `first_hit_date` / `last_hit_date` | TEXT | 最初・最後の急騰日 | `surge_events` から計算 |
| `updated_at` | TEXT | 更新日時（UTC） | 同期した時刻 |

人気スコア（[scripts/popularity.py](../scripts/popularity.py)）:

```
1 回の急騰の重み = min(前日比% / 10, 3) × 0.5 ^ (経過日数 / 30)
人気スコア       = 直近 365 日の急騰の重みの合計
```

同じ銘柄が株価DB に何度も出るほど加算され、30 日で重みが半分になる。

### 6-3. `monthly_prices`（月次株価）

主キーは `(ticker, ym)`。**Notion ではなく Yahoo Finance から取得** する。

| 列 | 型 | 意味 |
|---|---|---|
| `ticker` | TEXT | 銘柄コード |
| `ym` | TEXT | 年月（例 `2026-09`） |
| `close` | DOUBLE | その月の最後の取引日の終値（調整後） |

- **どの銘柄を取るか** は株価DB（`tickers`）で決まり、**数字** は Yahoo Finance から取る
- 同期で初めて出てきた銘柄だけ、最大 20 年分（最大 240 行）を取得する。2 回目以降は取り直さない
- 株式分割・配当を反映した **調整後終値**。昔の値は当時の実際の株価と違うことがある
- 取得した月の行は、取得時点の暫定値（月末の終値ではない）
- 「銘柄の過去株価で計算」モードで使う。「年率を維持」モードでは使わない
- DB ツールは先頭 100 行程度しか表示しないことが多い。1 銘柄で最大 240 行あるため、一覧画面では 2 銘柄しか見えないことがある。全体は SQL で確認する（7 章）

### 6-4. `scenarios`（保存したシミュレーション条件）

シミュレーション条件（モード、銘柄、年率、初期投資額、積立額、期間、最終評価額）を保存する入れ物。
**現在は未使用（空）**。画面に「条件を保存」機能を付けるときに使う想定。

---

## 7. データの確認

Neon の **SQL Editor**（または任意の PostgreSQL クライアント）で実行する。

```sql
-- 急騰日ごとの件数
SELECT date, count(*) FROM surge_events GROUP BY date ORDER BY date DESC;

-- ある日の急騰銘柄
SELECT e.ticker, t.name, e.prev_close, e.close, e.change_pct
FROM surge_events e JOIN tickers t USING (ticker)
WHERE e.date = '2026-10-22' ORDER BY e.change_pct DESC;

-- 人気ランキング
SELECT ticker, name, hit_count, popularity_score, last_hit_date
FROM tickers ORDER BY popularity_score DESC;

-- 月次株価の銘柄ごとの件数・期間
SELECT ticker, count(*) AS months, min(ym) AS from_ym, max(ym) AS to_ym
FROM monthly_prices GROUP BY ticker ORDER BY ticker;
```

### 現在のデータ（2026-10-25 の初回同期時点）

| 項目 | 件数 |
|---|---|
| 株価DB の行 | 35 行（うち 1 行スキップ） |
| `surge_events` | 34 件（9 日分: 2026-10-09 〜 10-22） |
| `tickers` | 34 銘柄 |
| `monthly_prices` | 33 銘柄・4,581 行 |

- スキップした行: 2026-09-25 の「例」（レポートからティッカーを特定できない。見本の行と思われる）
- ウリドキ（418A.T）: Yahoo Finance にデータがなく月次株価 0 件。年率維持モードでは使えるが、過去株価モードでは選べない

### ローカルの確認用 DB（Docker）

動作確認用に、6 銘柄だけ入れた PostgreSQL がローカルにある（Neon とは別物）。

```
接続先: postgresql://stocksim:stocksim@localhost:5433/stocksim
起動 / 停止: docker start stocksim-pg / docker stop stocksim-pg
psql: docker exec -it stocksim-pg psql -U stocksim -d stocksim
```

---

## 8. 自動化（GitHub Actions）

[.github/workflows/sync-notion.yml](../.github/workflows/sync-notion.yml) で「テスト → 同期 → JSON 書き出し → Cloudflare Pages にデプロイ」をまとめて実行できる。

1. GitHub のリポジトリ → Settings → Secrets and variables → Actions に登録
   - `NOTION_TOKEN`、`DATABASE_URL`（Neon）
   - デプロイする場合は `CLOUDFLARE_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`
2. Actions タブから手動実行（直近何日ぶんを同期するか指定できる）
3. 定期実行する場合は、ワークフローの `schedule`（平日 17:00 JST）のコメントを外す

---

## 9. 関連ファイル

| ファイル | 役割 |
|---|---|
| [scripts/sync_notion.py](../scripts/sync_notion.py) | Notion 株価DB → PostgreSQL の同期 |
| [scripts/export_snapshot.py](../scripts/export_snapshot.py) | DB → 画面用 JSON（`public/data/surges.json`、`public/data/prices/*.json`） |
| [scripts/db.py](../scripts/db.py) | PostgreSQL / D1 / SQLite のクライアント、`.env` の読み込み |
| [scripts/detect_surges.py](../scripts/detect_surges.py) | yfinance で +10% 急騰を自動検出（Notion を使わない経路） |
| [scripts/popularity.py](../scripts/popularity.py) | 人気スコアの計算 |
| [scripts/fetch_prices.py](../scripts/fetch_prices.py) | Yahoo Finance から月次株価を取得 |
| [db/schema_pg.sql](../db/schema_pg.sql) | PostgreSQL のスキーマ |
| [public/index.html](../public/index.html) / [public/sim.py](../public/sim.py) | 画面 / シミュレーション計算 |
| [tests/test_notion_sync.py](../tests/test_notion_sync.py) | 同期・書き出し・年率維持モードのテスト（`python -m unittest discover tests`） |
| `.env` / [.env.example](../.env.example) | トークン・接続 URL（`.env` は Git 管理外） |

---

## 10. 今後の課題

- **月次株価の更新**: 保存済み銘柄の月次株価は取り直さないため、直近月の値が古くなる。同期のたびに直近数か月を取り直す処理を足すとよい
- **Worker API**: 既存の Cloudflare Worker（[worker/](../worker/)）は D1 を読む作りのまま。画面は静的 JSON 経由で PostgreSQL のデータを表示している。リアルタイムに DB を読みたい場合は、Worker から PostgreSQL に接続する構成（Hyperdrive など）が必要
- **シナリオ保存**: `scenarios` テーブルは未使用
