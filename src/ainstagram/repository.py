"""drafts / queue / post_history에 대한 CRUD 함수 모음."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from . import constants as c
from .models import Draft, HistoryEntry, QueueItem


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- drafts ----

def create_draft(
    conn: sqlite3.Connection,
    category: str,
    topic: str,
    caption: str,
    slides: list[dict[str, Any]],
    thumbnail_url: Optional[str] = None,
    embedding: Optional[list[float]] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO drafts (created_at, category, topic, caption, slides_json, thumbnail_url, embedding_json, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _now(),
            category,
            topic,
            caption,
            json.dumps(slides, ensure_ascii=False),
            thumbnail_url,
            json.dumps(embedding) if embedding is not None else None,
            c.DRAFT_PENDING,
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_pending_drafts(conn: sqlite3.Connection) -> list[Draft]:
    rows = conn.execute(
        "SELECT * FROM drafts WHERE status = ? ORDER BY created_at DESC", (c.DRAFT_PENDING,)
    ).fetchall()
    return [Draft.from_row(r) for r in rows]


def get_draft(conn: sqlite3.Connection, draft_id: int) -> Optional[Draft]:
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    return Draft.from_row(row) if row else None


def discard_draft(conn: sqlite3.Connection, draft_id: int) -> None:
    conn.execute("UPDATE drafts SET status = ? WHERE id = ?", (c.DRAFT_DISCARDED, draft_id))
    conn.commit()


def approve_draft(conn: sqlite3.Connection, draft_id: int, priority: int = 100) -> None:
    conn.execute(
        "UPDATE drafts SET status = ?, priority = ? WHERE id = ?",
        (c.DRAFT_APPROVED, priority, draft_id),
    )
    conn.commit()


# ---- queue ----

def enqueue(
    conn: sqlite3.Connection,
    draft_id: int,
    caption: str,
    image_urls: list[str],
    priority: int = 100,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO queue (draft_id, caption, image_urls_json, priority, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (draft_id, caption, json.dumps(image_urls), priority, c.QUEUE_QUEUED, _now()),
    )
    conn.commit()
    return cur.lastrowid


def next_in_queue(conn: sqlite3.Connection) -> Optional[QueueItem]:
    row = conn.execute(
        """
        SELECT * FROM queue WHERE status = ?
        ORDER BY priority ASC, created_at ASC LIMIT 1
        """,
        (c.QUEUE_QUEUED,),
    ).fetchone()
    return QueueItem.from_row(row) if row else None


def mark_queue_item_published(conn: sqlite3.Connection, queue_id: int) -> None:
    conn.execute("UPDATE queue SET status = ? WHERE id = ?", (c.QUEUE_PUBLISHED, queue_id))
    conn.commit()


def set_priority(conn: sqlite3.Connection, queue_id: int, priority: int) -> None:
    conn.execute("UPDATE queue SET priority = ? WHERE id = ?", (priority, queue_id))
    conn.commit()


# ---- post_history ----

def insert_history(
    conn: sqlite3.Connection,
    category: str,
    topic: str,
    caption: str,
    instagram_media_id: Optional[str] = None,
    difficulty_level: Optional[int] = None,
    embedding: Optional[list[float]] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO post_history
            (published_at, category, topic, caption, difficulty_level, embedding_json, instagram_media_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _now(),
            category,
            topic,
            caption,
            difficulty_level,
            json.dumps(embedding) if embedding is not None else None,
            instagram_media_id,
        ),
    )
    conn.commit()
    return cur.lastrowid


def recent_history(
    conn: sqlite3.Connection, category: Optional[str] = None, limit: int = 30
) -> list[HistoryEntry]:
    if category:
        rows = conn.execute(
            "SELECT * FROM post_history WHERE category = ? ORDER BY published_at DESC LIMIT ?",
            (category, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM post_history ORDER BY published_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [HistoryEntry.from_row(r) for r in rows]
