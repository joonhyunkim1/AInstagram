"""SQLite 커넥션 + 스키마.

GitHub Actions 환경은 실행마다 새로 뜨기 때문에, 이 DB 파일 자체를
저장소에 커밋해서 영속시키는 방식으로 간다 (외부 DB 서버 비용 없이 처리).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DB_PATH = ROOT_DIR / "data" / "ainstagram.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    category TEXT NOT NULL,
    topic TEXT NOT NULL,
    caption TEXT NOT NULL,
    slides_json TEXT NOT NULL,
    thumbnail_url TEXT,
    embedding_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER
);

CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id),
    caption TEXT NOT NULL,
    image_urls_json TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    published_at TEXT NOT NULL,
    category TEXT NOT NULL,
    topic TEXT NOT NULL,
    caption TEXT NOT NULL,
    difficulty_level INTEGER,
    embedding_json TEXT,
    instagram_media_id TEXT
);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
