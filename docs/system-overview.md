# 株価シミュレーター：全体構成ガイド

最終更新: 2026-10-25

このアプリの全体像（何がどこで動き、どうつながっているか）をまとめる。
Notion → PostgreSQL 同期の手順とテーブルの詳細は [notion-postgres-sync.md](notion-postgres-sync.md) を参照。

---

## 1. 何をするアプリか

Notion の「📈 株価DB」に記録した **前日比 +10% 以上の急騰銘柄** を確認し、積立・一括投資をシミュレーションする。

| URL | 用途 | 鍵 |
|---|---|---|
| https://stock-simulator.tryoma0227.workers.dev | 公開画面（シミュレーター） | なし（誰でも見られる） |
| https://stock-simulator-admin.tryoma0227.workers.dev | 管理画面 | Cloudflare Access（メールのワンタイム PIN） |

### 画面の構成（公開画面）

1. **前日比 +10% 以上の急騰銘柄**：急騰日を選び、その日の銘柄・終値・前日比・前日終値・キーワードを表示。
   銘柄名は Yahoo!ファイナンス（`https://finance.yahoo.co.jp/quote/{ticker}`）へのリンク。行をクリックするとその銘柄で計算する
2. **条件**：モード・銘柄・想定年率・初期投資額・毎月の積立額・期間
3. **結果のタイル**：最終評価額・元本合計・損益・騰落率
4. **一括で投資した場合の利益**（年率維持モードのみ）：10 万円の場合は常に表示し、初期投資額を入れるとその行も追加
   - 1 日だけ：前日終値で買い、急騰日の終値で売った場合
   - N 年保有：急騰日の終値で買い、年率を維持して N 年後に評価した場合
5. **資産推移グラフ**：評価額・元本。年率維持モードでは想定株価（紫の点線・右目盛り）も重ねる
6. **人気銘柄ランキング**：急騰日が新しい順、同じ日の中は人気スコア順。行をクリックすると過去株価モードで計算する

### 3 つのモード

| モード | 計算 | 使うデータ |
|---|---|---|
| 急騰銘柄が年率を維持（初期表示） | 急騰日の終値を起点に、株価が想定年率（初期値 10%）で上がり続けた場合の積立 | `surges.json` の終値 |
| 想定年率で計算 | 一定の年率で毎月複利運用 | なし |
| 銘柄の過去株価で計算 | 実際の月末終値で毎月同額を買い付け（ドルコスト平均法） | `data/prices/*.json` |

> 年率を維持する計算では、**評価額は積立額と年率だけで決まるため、どの銘柄でも同じ**になる。
> 銘柄ごとに変わるのは、起点の株価・N 年後の想定株価・保有株数・1 日だけの利益。

---

## 2. 全体の流れ

```
[Notion]  📈 株価DB（急騰記録）＋ stock_reports（銘柄レポート。ティッカーを読む）
    │ Notion API（読み取りのみ）
    ▼
[GitHub Actions]  平日 23:50 JST（sync-notion.yml）
    ├─ scripts/sync_notion.py     Notion → Neon に同期、月次株価（Yahoo Finance）を取得、人気スコアを再計算
    ├─ scripts/export_snapshot.py Neon → public/data/surges.json ＋ public/data/prices/*.json
    └─ wrangler deploy            2 つの Worker にアップロード
    ▼
[Neon（PostgreSQL）]  tickers / surge_events / monthly_prices / scenarios
    ▲
    │ 管理画面の API だけが接続（読み書き）
[Cloudflare Workers]
    ├─ stock-simulator        public/ を配るだけ（プログラム・シークレットなし）
    └─ stock-simulator-admin  管理画面（admin/public/）＋ API（admin/src/）。Access で保護
    ▼
[ブラウザ]  index.html ＋ sim.py（Pyodide でブラウザ内の Python として実行）＋ Chart.js
```

| サービス | 役割 |
|---|---|
| Notion | 急騰銘柄を記録する場所（データの入口） |
| Neon | データをためておく場所（PostgreSQL） |
| GitHub | コードの保管と、平日 23:50 の自動実行（Actions） |
| Cloudflare Workers | 画面をインターネットに公開する場所。PC を切っていても、スマホや外出先から URL で開ける |
| Yahoo Finance | 月次株価の取得元（yfinance） |

---

## 3. 画面（HTML）と Python のつながり

計算は Python（[public/sim.py](../public/sim.py)）、画面の部品・グラフ・入力は JavaScript（[public/index.html](../public/index.html)）が担当する。
Python は **サーバーではなく、閲覧者のブラウザの中**（Pyodide）で動く。

```
① ページを開く
   index.html が Pyodide（ブラウザ用 Python）と Chart.js を CDN から読み込む
② sim.py を取ってきて実行し、関数を JS から呼べるようにする
     py = await loadPyodide();
     py.runPython(await (await fetch("sim.py")).text());
     runSim  = py.globals.get("run");
     runHist = py.globals.get("historical_annual_rate");
     runLump = py.globals.get("surge_lump_sums");
③ 入力が変わるたびに update()（JS）
     → runSim(...) で計算 → .toJs() で JS の形に変換 → drawChart()（Chart.js）・表・タイルを更新
```

| JS の変数 | sim.py の関数 | 計算すること |
|---|---|---|
| `runSim` | `run` | 積立の毎月の推移（モードに応じて `simulate_from_price` / `simulate_fixed` / `simulate_historical`） |
| `runHist` | `historical_annual_rate` | 過去株価の実績年率 |
| `runLump` | `surge_lump_sums` | 一括投資の利益（1 日だけ / N 年保有） |

- JS → Python：数値はそのまま。過去株価の配列は `py.toPy()` で Python のリストに変換して渡す
- Python → JS：辞書・リストは `.toJs({ dict_converter: Object.fromEntries })` で変換
- `sim.py` はブラウザの外でも普通の Python として動く（`python public/sim.py`、テストも同じファイルを使う）

### 外部ライブラリ（CDN から読み込み。プロジェクト内にファイルはない）

| ライブラリ | 読み込み元 | 用途 |
|---|---|---|
| Chart.js 4.4.1 | cdnjs.cloudflare.com | グラフ（`new Chart(...)`） |
| Pyodide 0.26.4 | cdn.jsdelivr.net | ブラウザ内で Python を動かす（初回は約 10MB を読み込むため数秒かかる） |

---

## 4. 画面が読むデータ（public/data/）

| ファイル | 中身 | 使うモード | 作る場所 |
|---|---|---|---|
| `surges.json` | 急騰日ごとの銘柄一覧（急騰のあった日を新しい順に 30 日ぶん）と、人気ランキング用の銘柄一覧 | 急騰表・年率維持・ランキング | `export_snapshot.py` |
| `prices/<ticker>.json` | 1 銘柄の月次終値（例 `4714_T.json` = 4714.T） | 過去株価モードのみ | `export_snapshot.py` |
| `index.json` / `SAMPLE.json` | 動作確認用の架空データ | `surges.json` が無いときだけ | 手書き |

- `surges.json` と `prices/` は **Git の管理外**。GitHub Actions が毎回 Neon から作り直してアップロードする
- `prices/` は選んだ銘柄のファイルだけを読み込み、一度読んだ銘柄はブラウザが覚えている（`priceCache`）
- 月次株価がない銘柄（例：ウリドキ 418A.T）はファイルが作られず、過去株価モードの選択肢に出ない

---

## 5. Cloudflare Workers の構成

### 2 つの Worker に分けている理由

Cloudflare Access の「Workers」の宛先は **Worker 全体しか保護できず、パス（`/admin` だけ）を指定できない**。
1 つの Worker にすると、管理画面に鍵をかけたときに公開画面まで閉じてしまうため、管理画面を別の Worker にした。

| 利点 | 内容 |
|---|---|
| 公開画面は開けたまま | 管理画面の Worker 全体にだけ鍵をかけられる |
| 安全性 | 公開画面の Worker は Neon の接続 URL もプログラムも持たない |
| 影響の分離 | 管理画面の不具合が公開画面に及ばない |
| 無料枠の節約 | 公開画面へのアクセスではプログラムが動かない |

独自ドメインを使うようになれば、「パブリックホスト名」の宛先でパスを指定して 1 つにまとめることもできる。

### 設定ファイル

| ファイル | Worker | 中身 |
|---|---|---|
| [wrangler.jsonc](../wrangler.jsonc) | stock-simulator | `public/` を静的ファイルとして配る（`main` なし） |
| [admin/wrangler.jsonc](../admin/wrangler.jsonc) | stock-simulator-admin | `admin/public/` を配り、`/api/*` だけ `admin/src/worker.js` が処理する |

### 本番（プロダクション）とデプロイごとの URL

- `wrangler deploy` を実行した時点で、その版が **そのまま本番（配信割合 100%）** になる。「本番を作る」別の手順はない
- 本番 URL（`○○.workers.dev`）は 1 つの「正式な入口」。版ごとのプレビュー URL は、公開前の確認やロールバック（前の版に戻す）用
- このアプリは毎回そのまま本番に出しているので、プレビューはほとんど使わない

### デプロイと Git の関係

- `wrangler deploy` は **手元のフォルダのファイルを直接** Cloudflare にアップロードする。コミットしたかどうかは関係ない
- 平日 23:50 の自動実行は **GitHub に push 済みのファイル** で動く。変更したら push を忘れないこと

---

## 6. 管理画面

| できること | 内容 |
|---|---|
| テーブル表示 | Neon の 4 テーブルの行数と中身（100 行ずつ、銘柄コードで絞り込み）。表示のみ |
| 急騰記録の手動追加 | 銘柄コード・銘柄名・急騰日・前日終値・終値・キーワード。前日比は自動計算、+10% 以上のみ。`source = 'manual'` で保存し、その銘柄の人気スコアもすぐ再計算 |
| 削除 | 手動追加した記録だけ（Notion から同期した記録は次の同期で戻るため削除不可） |

- 公開画面への反映は、次の自動更新（平日 23:50）か GitHub Actions の手動実行のとき
- 手動追加した銘柄の月次株価は、次の同期で Yahoo Finance から取得される

### 保護の仕組み（二重）

1. **Cloudflare Access**（Zero Trust → アクセス制御 → アプリケーション）
   - 宛先: Workers ／ スコープ `stock-simulator-admin` ／ タイプ「Worker のプロダクション URL とプレビュー URL」
   - ポリシー: 許可 ／ メール `tryoma0227@gmail.com` ／ ワンタイム PIN
   - **スコープを `stock-simulator` にしないこと**（公開画面までログインが必要になる）
2. **API 側の確認**（[admin/src/admin/auth.js](../admin/src/admin/auth.js)）
   - Access が付ける署名付きトークン（`Cf-Access-Jwt-Assertion`）の署名・発行元・AUD・有効期限を確認し、だめなら 401
   - `ACCESS_TEAM_DOMAIN`：トークンを発行したチームか（`autumn-shape-c7ac.cloudflareaccess.com`。署名の鍵の取得元）
   - `ACCESS_AUD`：この管理画面のアプリケーション宛てか（Access アプリを作り直すと変わる）
   - どちらもパスワードではない（知っていても、許可したメールの人しかログインできない）

---

## 7. 鍵・接続情報の置き場所

| 名前 | 用途 | 手元 `.env` | GitHub Secrets | Cloudflare（admin Worker） |
|---|---|---|---|---|
| `NOTION_TOKEN` | Notion の株価DB を読む | ◯ | ◯ | — |
| `DATABASE_URL` | Neon に接続 | ◯ | ◯ | ◯ |
| `CLOUDFLARE_API_TOKEN` | Workers へのデプロイ（権限: Workers スクリプト：編集、Cloudflare Pages：編集） | ◯ | ◯ | — |
| `CLOUDFLARE_ACCOUNT_ID` | デプロイ先のアカウント | — | ◯ | — |
| `ACCESS_TEAM_DOMAIN` / `ACCESS_AUD` | 管理画面のログイン確認 | ◯（控え） | — | ◯ |

- `.env`・`admin/.dev.vars`・`node_modules/` は Git の管理外。**トークンを `.env.example` に書かない**（コミットされる）
- 公開画面の Worker にはシークレットを置かない
- トークンを作り直したときは、`.env` と GitHub Secrets（と必要なら Cloudflare）を両方書き換える

---

## 8. 自動実行（GitHub Actions）

[.github/workflows/sync-notion.yml](../.github/workflows/sync-notion.yml)：平日 23:50 JST（cron は UTC で `50 14 * * 1-5`）

| 順番 | ステップ |
|---|---|
| 1 | テスト（`python -m unittest discover tests`） |
| 2 | Notion → Neon の同期（急騰日が直近 7 日の行） |
| 3 | 画面用 JSON の書き出し |
| 4 | `npm ci`（管理画面の API が使う Neon のドライバー） |
| 5 | 公開画面の Worker と管理画面の Worker にデプロイ（wrangler 4.152.0 を指定） |

- 途中で失敗するとデプロイされず、公開画面は前回の内容のまま。GitHub からメールで通知が届く
- GitHub の混雑で数分〜数十分遅れることがある。土日は動かない
- すぐ反映したいときは Actions →「sync Notion → PostgreSQL & deploy」→「Run workflow」
- 公開リポジトリは 60 日間コミットがないと定期実行が止まる（Actions の画面から再び有効にできる）

---

## 9. よく使うコマンド

```powershell
# 同期と画面用 JSON（.env の値を自動で読む）
.venv\Scripts\python scripts\sync_notion.py            # 直近 7 日
.venv\Scripts\python scripts\sync_notion.py --all      # 全期間
.venv\Scripts\python scripts\export_snapshot.py

# テスト
.venv\Scripts\python -m unittest discover tests

# 手元で確認
npm run dev          # 公開画面  http://127.0.0.1:8788
npm run dev:admin    # 管理画面  http://127.0.0.1:8789（admin/.dev.vars が必要。本物の Neon に書き込まれる）

# デプロイ（手元から）
npm run deploy       # 2 つの Worker の両方
```

---

## 10. これまでの経緯（2026-10-25）

| 項目 | 内容 |
|---|---|
| 同期 | Notion 株価DB → Neon（PostgreSQL）。初回同期で 34 件・34 銘柄・月次株価 33 銘柄（4,581 行） |
| 画面 | 急騰表、年率維持モード、Yahoo!ファイナンスへのリンク、想定株価の線、一括投資の表、ランキングの並び順を追加 |
| 公開 | Cloudflare Pages で公開 → Access の都合で Cloudflare Workers に移行し、公開画面と管理画面を別の Worker に分割 |
| 旧構成 | 旧 Pages プロジェクト（stock-simulator-eg9.pages.dev）は削除済み。[worker/](../worker/)（D1 を読む Python Worker）と [deploy.yml](../.github/workflows/deploy.yml)（Pages 用）は使っていない |

---

## 11. 今後の課題

- **月次株価の更新**：保存済み銘柄の月次株価は取り直さないため、直近月の値が古くなる。同期のたびに直近数か月を取り直すとよい
- **シナリオ保存**：`scenarios` テーブルは未使用
- **旧構成の整理**：`worker/` と `deploy.yml` は削除してよい
- **アイコン**：`favicon.png`（タブ用。六角形の外側は透明）と `apple-touch-icon.png`（iPhone のホーム画面用。白背景）。
  元画像が 207×230 と小さいため 180px まで。高解像度の元画像があれば作り直せる
