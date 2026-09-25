"""D1（本番）とローカル SQLite（開発・テスト）を同じ書き方で扱うための薄いクライアント。

- D1Client    : Cloudflare D1 の REST API を呼ぶ（GitHub Actions などサーバー外から書き込む用）
- SqliteClient: ローカルの SQLite ファイル。スキーマ・SQL は D1 と共通
"""
import os
import sqlite3
from pathlib import Path

import requests

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
D1_MAX_PARAMS = 100  # D1 は 1 クエリあたりのバインド数が 100 まで


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
    """引数に SQLite のパスがあればローカル、なければ環境変数から D1 に接続する。"""
    if sqlite_path:
        return SqliteClient(sqlite_path)
    missing = [k for k in ("CLOUDFLARE_ACCOUNT_ID", "D1_DATABASE_ID", "CLOUDFLARE_API_TOKEN") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"D1 に接続するには環境変数 {', '.join(missing)} が必要です（ローカルなら --sqlite を指定）")
    return D1Client(
        os.environ["CLOUDFLARE_ACCOUNT_ID"],
        os.environ["D1_DATABASE_ID"],
        os.environ["CLOUDFLARE_API_TOKEN"],
    )
