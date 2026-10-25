# 積立・リターン シミュレーター（雛形）

銘柄の株価データを使い、毎月の積立投資をシミュレーションします。

- **急騰銘柄が年率を維持モード**：前日比 +10% 以上に急騰した銘柄の **急騰日の終値** を起点に、株価が想定年率（初期値 **10%**）で上がり続けた場合（想定株価・保有株数も表示）
- **想定年率モード**：一定の年率（初期値 **10%**）で毎月複利運用した場合
- **過去株価モード**：実際の月末終値で毎月同じ金額を買い付けた場合（ドルコスト平均法）

計算は **ブラウザ内の Python（Pyodide）** で行います。そのため Cloudflare Pages の静的ホスティングだけで動きます。

銘柄は **Notion の「株価DB」（急騰記録）を PostgreSQL に同期したもの** を参照します（D1 + `detect_surges.py` の自動検出も引き続き使えます）。
同じ企業が何度も保存されるほど「人気スコア」が上がり、一覧やランキングで上位に表示されます。

## データの流れ（Notion → PostgreSQL）

```
[Notion 📈 株価DB]  銘柄（→ 銘柄レポートページ）・急騰日・終値・前日終値・前日比率・キーワード
        │ Notion API（読み取りのみ）
        ▼
[GitHub Actions: sync-notion.yml]
  scripts/sync_notion.py
    ├─ 急騰日が直近 N 日の行を取得
    ├─ 銘柄レポートページの「ティッカー | 4398.T」からティッカーを特定（同期済みの行は読み直さない）
    ├─ 前日比 +10% 以上 → surge_events に保存（Notion 側の修正は上書き）
    ├─ 初登場の銘柄 → monthly_prices に月次株価 20 年分を保存（yfinance）
    └─ 人気スコアを再計算
        │ DATABASE_URL
        ▼
   [PostgreSQL（Neon / Supabase など）]
        │ scripts/export_snapshot.py
        ▼
   public/data/surges.json ＋ public/data/prices/*.json
        │ wrangler pages deploy
        ▼
   [Cloudflare Pages: index.html + sim.py (Pyodide)]
```

> 「年率 10% を維持」した場合の評価額は、毎月の積立額と年率だけで決まるため **どの銘柄でも同じ** になります。
> 銘柄ごとに変わるのは、起点の株価（急騰日の終値）・N 年後の想定株価・保有株数です。

## データの流れ（D1 / 自動検出）

```
[GitHub Actions 平日 16:30]
  scripts/detect_surges.py
    ├─ 対象銘柄の日足を取得（universe.txt または --jpx で東証の全銘柄）
    ├─ 前日比 +10% 以上 → surge_events に保存（同じ銘柄・同じ日は 1 回だけ）
    ├─ 初登場の銘柄 → monthly_prices に月次株価 20 年分を保存
    └─ 全銘柄の hit_count / popularity_score を再計算
                    │ D1 REST API
                    ▼
               [Cloudflare D1]
                    ▲
                    │ GET /api/tickers（人気順） / GET /api/prices
          [Python Worker (worker/)]
                    ▲
                    │ fetch
   [Cloudflare Pages: index.html + sim.py (Pyodide)]
```

## 人気スコア（重み付け）

`scripts/popularity.py`

```
1 回の急騰の重み = min(前日比% / 10, 3) × 0.5 ^ (経過日数 / 30)
人気スコア       = 直近 365 日の急騰の重みの合計
```

- **同じ企業が再保存されるたびに加算**：3 回急騰した銘柄は 1 回の銘柄より上位になる
- **上昇幅**：+10% で 1.0、+20% で 2.0（上限 3.0。連続ストップ高などで極端にならないようにする）
- **鮮度**：30 日で重みが半分になる。昔は人気だった銘柄は徐々に順位が下がる

数値（しきい値・半減期・上限）は `popularity.py` 冒頭の定数で調整できます。

## 構成

```
stock_simulation/
├─ public/                 ← Cloudflare Pages にデプロイ
│  ├─ index.html           画面（Chart.js でグラフ・表を描画）
│  ├─ sim.py               シミュレーションロジック（Python / 標準ライブラリのみ）
│  └─ data/
│     ├─ index.json        銘柄一覧
│     └─ SAMPLE.json       動作確認用の架空データ（実績年率 10%）
├─ public/config.js        Worker API の URL（空なら静的サンプルで動作）
├─ scripts/
│  ├─ detect_surges.py     +10% 急騰の検出 → D1 保存 → 人気スコア更新
│  ├─ popularity.py        人気スコア（重み付け）の計算
│  ├─ sync_notion.py       Notion 株価DB → PostgreSQL の同期
│  ├─ export_snapshot.py   DB → 画面用 JSON（surges.json / prices/*.json）
│  ├─ db.py                PostgreSQL / D1（REST API）/ ローカル SQLite のクライアント
│  └─ fetch_prices.py      月次終値の取得（静的 JSON 出力にも使用）
├─ universe.txt            急騰判定の対象銘柄（--jpx で東証の全銘柄）
├─ db/schema.sql           D1 / SQLite スキーマ（tickers / surge_events / monthly_prices / scenarios）
├─ db/schema_pg.sql        PostgreSQL スキーマ（列は schema.sql と同じ）
├─ tests/                  python -m unittest discover tests
└─ worker/                 D1 を読む Python Worker API
```

## ローカルで動かす

```bash
cd public
python -m http.server 8000     # http://localhost:8000
python sim.py                  # ロジック単体の確認
```

## Notion の株価DB を PostgreSQL に同期する

準備:

1. Notion で [インテグレーション](https://www.notion.so/profile/integrations) を作成し、トークンを控える
2. 「📈 株価DB」と銘柄レポートの親ページ（stock_reports）の「接続」にそのインテグレーションを追加する
   （レポートページが読めないとティッカーを特定できず、その行はスキップされる）
3. Neon / Supabase などで PostgreSQL を作成し、接続 URL（`postgresql://...?sslmode=require`）を控える

```bash
pip install -r requirements.txt
cp .env.example .env    # .env に NOTION_TOKEN と DATABASE_URL を書く（.env は Git の管理外）
python scripts/sync_notion.py --init-schema --all   # 初回：テーブル作成＋全期間を同期
python scripts/sync_notion.py                       # 以降：急騰日が直近 7 日の行を同期（--days で変更）
python scripts/export_snapshot.py                   # 画面用の JSON を public/data/ に書き出す
cd public && python -m http.server 8000             # 確認
```

GitHub Actions の `.github/workflows/sync-notion.yml` では、同期 → 書き出し → Pages へのデプロイをまとめて行います。
シークレット `NOTION_TOKEN`・`DATABASE_URL`・`CLOUDFLARE_API_TOKEN`・`CLOUDFLARE_ACCOUNT_ID` を登録してください。

PostgreSQL での保存を確認するテスト（空の DB を指定）:

```bash
TEST_DATABASE_URL=postgresql://postgres:test@localhost:5432/postgres python -m unittest discover tests
```

## 急騰銘柄を DB に保存する（yfinance で自動検出）

```bash
pip install -r requirements.txt
python -m unittest discover tests                            # ネットワーク不要のテスト
python scripts/detect_surges.py --sqlite local.db --init-schema  # ローカル SQLite で試す
python scripts/detect_surges.py --jpx                          # D1 に保存（下記の環境変数が必要）
```

D1 に書き込むには、環境変数 `CLOUDFLARE_ACCOUNT_ID`・`D1_DATABASE_ID`・`CLOUDFLARE_API_TOKEN`（D1 の編集権限）が必要です。
GitHub Actions の `.github/workflows/detect-surges.yml` では、同じ名前のシークレットを使います。

## 静的 JSON に株価を追加する（DB なしで動かす場合）

```bash
pip install -r requirements.txt
python scripts/fetch_prices.py 7203.T 6758.T ^N225 --years 20
```

## デプロイ（Cloudflare Pages）

```bash
npx wrangler pages deploy public --project-name=stock-simulator
```

`.github/workflows/deploy.yml` を使うと、株価の取得からデプロイまでを自動化できます。
リポジトリに `CLOUDFLARE_API_TOKEN` と `CLOUDFLARE_ACCOUNT_ID` をシークレットとして登録し、手動で実行するか `schedule` を有効にしてください。

## DB の選び方

| 用途 | 推奨 | 理由 |
|---|---|---|
| 株価の時系列（表示用） | **静的 JSON**（大きくなったら **R2**） | 読み取り専用で、まとめて取得するだけなので DB は不要。CDN から高速に配信できる |
| シナリオ保存・ユーザーデータ・銘柄の検索や集計 | **D1**（SQLite） | SQL が使え、Workers から直接読み書きできる。無料枠が大きい |
| API レスポンスのキャッシュ・設定値 | **KV** | キーと値で読むだけの用途に向く。結果整合性なので書き込みの多い用途には不向き |

急騰の記録・人気スコア・月次株価はすべて **D1** に保存します（銘柄数が数百〜数千、月次株価で数十万行程度なので D1 の範囲に収まります）。

D1 と API を準備する手順：

```bash
cd worker
npx wrangler d1 create stock-sim            # 表示された database_id を wrangler.toml に記入
npx wrangler d1 execute stock-sim --remote --file=../db/schema.sql
npx wrangler deploy                          # 表示された URL を public/config.js の SIM_API_BASE に設定
```

> Python Workers は機能追加が続いているため、API の書き方は公式ドキュメントで最新版を確認してください。
