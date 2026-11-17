"""SQLite 저장소. 프로토타입이라 ORM 없이 sqlite3만 사용한다."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from . import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    category TEXT,
    author TEXT,
    posted_at TEXT,
    ext_key TEXT UNIQUE,         -- 사이트 간 중복 제거 키 (예: ajou:379412)
    content TEXT,                -- NULL = 본문 아직 안 가져옴
    tags TEXT DEFAULT '[]',
    keywords TEXT DEFAULT '[]',
    summary TEXT DEFAULT '',
    deadline TEXT,
    pinned INTEGER DEFAULT 0,
    tagged_by TEXT,
    fetched_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notices_posted ON notices(posted_at DESC);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,          -- history | folder | keyword | click | bookmark
    item TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_signals_user ON signals(user_id);

CREATE TABLE IF NOT EXISTS interest_nodes (
    user_id TEXT NOT NULL,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,          -- category | topic
    weight REAL NOT NULL DEFAULT 0,
    sources TEXT DEFAULT '{}',   -- {"history": 3, "folder": 1}
    PRIMARY KEY (user_id, label)
);

CREATE TABLE IF NOT EXISTS interest_edges (
    user_id TEXT NOT NULL,
    a TEXT NOT NULL,
    b TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, a, b)
);

CREATE TABLE IF NOT EXISTS insights (
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    title TEXT,
    content_md TEXT NOT NULL,
    generated_by TEXT,
    ctx_hash TEXT,
    ctx_tokens_est INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, date)
);

CREATE TABLE IF NOT EXISTS bookmarks (
    user_id TEXT NOT NULL,
    notice_id INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, notice_id)
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose TEXT,                -- tagging | insight
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS crawl_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT,
    ok INTEGER,
    found INTEGER,
    new INTEGER,
    message TEXT,
    at TEXT DEFAULT (datetime('now'))
);
"""


def _connect() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def get_conn():
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db() -> None:
    with get_conn() as c:
        c.executescript(SCHEMA)


def row_to_notice(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["keywords"] = json.loads(d.get("keywords") or "[]")
    d["pinned"] = bool(d.get("pinned"))
    d.pop("content", None)
    return d
