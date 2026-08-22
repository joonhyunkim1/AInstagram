"""repository 계층이 주고받는 데이터 구조."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Draft:
    id: int
    created_at: str
    category: str
    topic: str
    caption: str
    slides: list[dict[str, Any]]
    thumbnail_url: Optional[str]
    embedding: Optional[list[float]]
    difficulty_level: Optional[int]
    status: str
    priority: Optional[int]

    @classmethod
    def from_row(cls, row) -> "Draft":
        return cls(
            id=row["id"],
            created_at=row["created_at"],
            category=row["category"],
            topic=row["topic"],
            caption=row["caption"],
            slides=json.loads(row["slides_json"]),
            thumbnail_url=row["thumbnail_url"],
            embedding=json.loads(row["embedding_json"]) if row["embedding_json"] else None,
            difficulty_level=row["difficulty_level"],
            status=row["status"],
            priority=row["priority"],
        )


@dataclass
class QueueItem:
    id: int
    draft_id: int
    caption: str
    image_urls: list[str]
    priority: int
    status: str
    created_at: str

    @classmethod
    def from_row(cls, row) -> "QueueItem":
        return cls(
            id=row["id"],
            draft_id=row["draft_id"],
            caption=row["caption"],
            image_urls=json.loads(row["image_urls_json"]),
            priority=row["priority"],
            status=row["status"],
            created_at=row["created_at"],
        )


@dataclass
class HistoryEntry:
    id: int
    published_at: str
    category: str
    topic: str
    caption: str
    difficulty_level: Optional[int]
    embedding: Optional[list[float]]
    instagram_media_id: Optional[str]

    @classmethod
    def from_row(cls, row) -> "HistoryEntry":
        return cls(
            id=row["id"],
            published_at=row["published_at"],
            category=row["category"],
            topic=row["topic"],
            caption=row["caption"],
            difficulty_level=row["difficulty_level"],
            embedding=json.loads(row["embedding_json"]) if row["embedding_json"] else None,
            instagram_media_id=row["instagram_media_id"],
        )
