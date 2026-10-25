"""PostgreSQL / D1 / ローカル SQLite を同じ書き方で扱うための薄いクライアント。

- PostgresClient: Neon / Supabase などの PostgreSQL（環境変数 DATABASE_URL）
- D1Client      : Cloudflare D1 の REST API を呼ぶ（GitHub Actions などサーバー外から書き込む用）
- SqliteClient  : ローカルの SQLite ファイル。スキーマ・SQL は D1 と共通

SQL のプレースホルダはどれも "?" で書く（PostgresClient が "%s" に読み替える）。
接続情報やトークンは、環境変数かプロジェクト直下の .env（.env.example を参照）に書く。
"""
import os
import sqlite3
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "db" / "schema.sql"
PG_SCHEMA_PATH = SCHEMA_PATH.with_name("schema_pg.sql")
D1_MAX_PARAMS = 100  # D1 は 1 クエリあたりのバインド数が 100 まで


def load_dotenv(path=ROOT / ".env"):
    """KEY=VALUE 形式の .env を環境変数に読み込む。すでに設定されている環境変数は上書きしない。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_dotenv()  # このモジュールを読み込むスクリプト（同期・書き出し・急騰検出）すべてで有効になる


class SqliteClient:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

    def query(self, sql, params=()):
        cur = self.conn.execute(sql, list(params))
        rows = [dict(r) for r in cur.fetchall()]
        self.conn.commit()
        return rows

    def init_schema(self):
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


class PostgresClient:
    def __init__(self, url):
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(url, row_factory=dict_row, autocommit=True)

    def query(self, sql, params=()):
        # SQL 中に "?" や "%" の文字リテラルは書かない前提（LIKE などが必要になったら見直す）
        with self.conn.cursor() as cur:
            cur.execute(sql.replace("?", "%s"), list(params) or None)
            return cur.fetchall() if cur.description else []

    def init_schema(self):
        self.conn.execute(PG_SCHEMA_PATH.read_text(encoding="utf-8"))


class D1Client:
    def __init__(self, account_id, database_id, api_token):
        self.url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/d1/database/{database_id}/query"
        )
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_token}"

    def query(self, sql, params=()):
        res = self.session.post(self.url, json={"sql": sql, "params": list(params)}, timeout=30)
        body = res.json()
        if not res.ok or not body.get("success"):
            raise RuntimeError(f"D1 query failed: {body.get('errors')} / SQL: {sql[:200]}")
        return body["result"][0].get("results", [])

    def init_schema(self):
        for stmt in SCHEMA_PATH.read_text(encoding="utf-8").split(";"):
            lines = [l for l in stmt.splitlines() if not l.strip().startswith("--")]
            if "\n".join(lines).strip():
                self.query(stmt)


def insert_many(db, head_sql, rows, tail_sql=""):
    """複数行 INSERT をバインド数の上限に合わせて分割実行する。

    head_sql: "INSERT INTO t (a, b) VALUES"   tail_sql: "ON CONFLICT ... DO UPDATE ..."
    """
    if not rows:
        return
    width = len(rows[0])
    per_stmt = max(1, D1_MAX_PARAMS // width)
    placeholder = "(" + ", ".join(["?"] * width) + ")"
    for i in range(0, len(rows), per_stmt):
        chunk = rows[i:i + per_stmt]
        sql = f"{head_sql} {', '.join([placeholder] * len(chunk))} {tail_sql}"
        db.query(sql, [v for row in chunk for v in row])


def connect(sqlite_path=None):
    """接続先を選ぶ。優先順: 引数の SQLite パス → 環境変数 DATABASE_URL（PostgreSQL）→ D1。"""
    if sqlite_path:
        return SqliteClient(sqlite_path)
    if os.environ.get("DATABASE_URL"):
        return PostgresClient(os.environ["DATABASE_URL"])
    missing = [k for k in ("CLOUDFLARE_ACCOUNT_ID", "D1_DATABASE_ID", "CLOUDFLARE_API_TOKEN") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"PostgreSQL なら DATABASE_URL、D1 なら {', '.join(missing)} を環境変数に設定してください（ローカルなら --sqlite）")
    return D1Client(
        os.environ["CLOUDFLARE_ACCOUNT_ID"],
        os.environ["D1_DATABASE_ID"],
        os.environ["CLOUDFLARE_API_TOKEN"],
    )
