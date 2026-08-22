"""대기열에서 다음 게시물을 꺼내 실제로 발행하고 이력에 남긴다."""
from __future__ import annotations

import sqlite3
from typing import Optional

from .. import repository as repo
from .instagram_client import InstagramClient


def publish_next(conn: sqlite3.Connection, instagram: InstagramClient) -> Optional[str]:
    """대기열이 비어있으면 None, 아니면 발행된 미디어 ID를 반환."""
    item = repo.next_in_queue(conn)
    if item is None:
        return None

    media_id = instagram.publish_carousel(item.image_urls, item.caption)
    repo.mark_queue_item_published(conn, item.id)

    draft = repo.get_draft(conn, item.draft_id)
    repo.insert_history(
        conn,
        category=draft.category if draft else "",
        topic=draft.topic if draft else "",
        caption=item.caption,
        instagram_media_id=media_id,
        difficulty_level=draft.difficulty_level if draft else None,
        embedding=draft.embedding if draft else None,
    )
    return media_id
