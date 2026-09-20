"""대기열에서 다음 게시물을 꺼내 실제로 발행하고 이력에 남긴다."""
from __future__ import annotations

import sqlite3
from typing import Optional

from .. import repository as repo
from ..config import AppConfig, get_config
from ..content.llm_client import LLMClient
from ..content.topic_generator import LLM, generate_and_store_drafts, pick_next_category
from ..images import composer, template
from ..images.ai_background import ImageBackend
from ..images.storage import S3LikeClient, upload_image
from ..models import QueueItem
from ..review.telegram_client import TelegramClient
from .instagram_client import InstagramClient


def build_publish_caption(topic: str, caption: str) -> str:
    """인스타그램에 올릴 본문: 제목, 빈 줄, 기존 캡션 순서."""
    topic = topic.strip()
    return f"{topic}\n\n{caption}" if topic else caption


def _auto_generate_and_enqueue(
    conn: sqlite3.Connection,
    image_backend: ImageBackend,
    storage_client: S3LikeClient,
    llm: LLM | None,
    cfg: AppConfig,
) -> Optional[QueueItem]:
    """대기열이 비었을 때 검수 없이 예비 게시물 하나를 만들어 바로 대기열에 넣는다."""
    llm = llm or LLMClient.from_config()
    category = pick_next_category(conn, cfg)
    draft_ids = generate_and_store_drafts(conn, category, llm, count=1)
    if not draft_ids:
        return None

    draft = repo.get_draft(conn, draft_ids[0])
    style = template.load_brand_style(cfg)
    images = composer.compose_slides(
        draft.topic, draft.category, draft.slides, image_backend, cfg.image.quality.final, style
    )
    image_urls = [
        upload_image(storage_client, image, f"posts/{draft.id}/{i}.jpg")
        for i, image in enumerate(images)
    ]
    repo.set_draft_images(conn, draft.id, image_urls)

    priority = repo.next_queue_priority(conn)
    repo.approve_draft(conn, draft.id, priority=priority)
    repo.enqueue(conn, draft.id, draft.caption, image_urls, priority=priority)
    return repo.next_in_queue(conn)


def publish_next(
    conn: sqlite3.Connection,
    instagram: InstagramClient,
    image_backend: ImageBackend | None = None,
    storage_client: S3LikeClient | None = None,
    telegram: TelegramClient | None = None,
    llm: LLM | None = None,
    cfg: AppConfig | None = None,
) -> Optional[str]:
    """대기열이 비어있으면 None, 아니면 발행된 미디어 ID를 반환.

    image_backend/storage_client가 주어졌는데 대기열이 비어있으면, 검수를 거치지 않고
    예비 게시물 하나를 새로 생성해서 바로 발행한다 (스케줄을 거르지 않기 위함).
    """
    item = repo.next_in_queue(conn)
    auto_generated = False
    if item is None:
        if image_backend is None or storage_client is None:
            return None
        cfg = cfg or get_config()
        item = _auto_generate_and_enqueue(conn, image_backend, storage_client, llm, cfg)
        if item is None:
            return None
        auto_generated = True

    draft = repo.get_draft(conn, item.draft_id)
    topic = draft.topic if draft else ""

    try:
        media_id = instagram.publish_carousel(
            item.image_urls, build_publish_caption(topic, item.caption)
        )
    except Exception as e:
        if telegram is not None:
            telegram.send_message(f"❌ 게시 실패: {topic}\n\n{str(e)[:1000]}")
        raise

    repo.mark_queue_item_published(conn, item.id)
    repo.insert_history(
        conn,
        category=draft.category if draft else "",
        topic=topic,
        caption=item.caption,
        instagram_media_id=media_id,
        difficulty_level=draft.difficulty_level if draft else None,
        embedding=draft.embedding if draft else None,
    )

    if telegram is not None:
        message = f"✅ 게시 완료: {topic}"
        if auto_generated:
            message += "\n\n(대기열이 비어 있어 검수 없이 바로 발행됨)"
        telegram.send_message(message)

    return media_id
