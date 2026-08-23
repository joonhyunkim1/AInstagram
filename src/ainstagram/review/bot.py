"""초안 검수 + 대기열 관리 흐름.

1) send_drafts_for_review: pending 상태인 초안들의 슬라이드 전체(실제 게시될 최종 품질)를
   렌더링해서 Telegram 앨범으로 전송. 이때 만든 이미지는 draft에 저장해둔다.
2) process_pending_reviews: getUpdates를 폴링해서 버튼 응답(채택/우선채택/폐기) 및
   /queue 명령어(대기열 조회/최우선으로/제거)를 처리
   - 채택 시 draft에 저장된 이미지를 그대로 재사용해서 대기열에 넣는다 (다시 만들지 않음)
"""
from __future__ import annotations

import io
import sqlite3
from typing import Any

from .. import constants as c
from .. import repository as repo
from ..config import AppConfig, get_config
from ..images import composer, template
from ..images.ai_background import ImageBackend
from ..images.storage import S3LikeClient, upload_image
from .telegram_client import TelegramClient

ACTION_APPROVE = "approve"
ACTION_APPROVE_TOP = "approve_top"
ACTION_DISCARD = "discard"
ACTION_QUEUE_BUMP = "queue_bump"
ACTION_QUEUE_REMOVE = "queue_remove"

QUEUE_COMMAND = "/queue"


def _render_and_store_images(
    conn: sqlite3.Connection,
    draft,
    image_backend: ImageBackend,
    storage_client: S3LikeClient,
    style: template.BrandStyle,
    quality: str,
) -> list[str]:
    images = composer.compose_slides(
        draft.topic, draft.category, draft.slides, image_backend, quality, style
    )
    image_urls = [
        upload_image(storage_client, image, f"posts/{draft.id}/{i}.jpg")
        for i, image in enumerate(images)
    ]
    repo.set_draft_images(conn, draft.id, image_urls)
    return image_urls


def send_drafts_for_review(
    conn: sqlite3.Connection,
    telegram: TelegramClient,
    image_backend: ImageBackend,
    storage_client: S3LikeClient,
    cfg: AppConfig | None = None,
) -> int:
    """pending 초안들의 전체 슬라이드(실제 게시될 최종 품질)를 렌더링해서 앨범으로 전송한다.

    여기서 만든 이미지를 draft에 저장해두기 때문에, 채택 시 다시 만들지 않는다.
    """
    cfg = cfg or get_config()
    style = template.load_brand_style(cfg)
    pending = repo.list_pending_drafts(conn)

    sent = 0
    for draft in pending:
        images = composer.compose_slides(
            draft.topic, draft.category, draft.slides, image_backend, cfg.image.quality.final, style
        )
        image_urls = [
            upload_image(storage_client, image, f"posts/{draft.id}/{i}.jpg")
            for i, image in enumerate(images)
        ]
        repo.set_draft_images(conn, draft.id, image_urls)

        image_bytes_list = []
        for image in images:
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=90)
            image_bytes_list.append(buf.getvalue())
        telegram.send_media_group(image_bytes_list)

        caption = f"[{draft.category}] {draft.topic}\n\n{draft.caption}\n\n{len(draft.slides)} slides"
        buttons = [
            {"text": "✅ 채택", "callback_data": f"{ACTION_APPROVE}:{draft.id}"},
            {"text": "⬆️ 최우선 채택", "callback_data": f"{ACTION_APPROVE_TOP}:{draft.id}"},
            {"text": "❌ 폐기", "callback_data": f"{ACTION_DISCARD}:{draft.id}"},
        ]
        telegram.send_message(caption, buttons)
        sent += 1
    return sent


def _approve_and_enqueue(
    conn: sqlite3.Connection,
    draft_id: int,
    image_backend: ImageBackend,
    storage_client: S3LikeClient,
    cfg: AppConfig,
    priority: int | None,
) -> None:
    draft = repo.get_draft(conn, draft_id)
    if draft is None:
        return

    image_urls = draft.image_urls
    if not image_urls:
        # 안전장치: 검수 단계에서 이미지가 저장되지 않은 경우에만 새로 렌더링한다
        style = template.load_brand_style(cfg)
        image_urls = _render_and_store_images(
            conn, draft, image_backend, storage_client, style, cfg.image.quality.final
        )

    final_priority = priority if priority is not None else repo.next_queue_priority(conn)
    repo.approve_draft(conn, draft_id, priority=final_priority)
    repo.enqueue(conn, draft_id, draft.caption, image_urls, priority=final_priority)


def send_queue_status(conn: sqlite3.Connection, telegram: TelegramClient) -> int:
    """현재 대기열을 순서대로 전송하고, 항목별로 최우선/제거 버튼을 붙인다."""
    items = repo.list_queue(conn)
    if not items:
        telegram.send_message("현재 대기열이 비어있습니다.")
        return 0

    for position, item in enumerate(items, start=1):
        draft = repo.get_draft(conn, item.draft_id)
        topic = draft.topic if draft else "(주제 정보 없음)"
        category = draft.category if draft else "?"
        text = f"{position}번째 게시 예정\n[{category}] {topic}\n우선순위 값: {item.priority}"
        buttons = [
            {"text": "⬆️ 최우선으로", "callback_data": f"{ACTION_QUEUE_BUMP}:{item.id}"},
            {"text": "❌ 대기열에서 제거", "callback_data": f"{ACTION_QUEUE_REMOVE}:{item.id}"},
        ]
        telegram.send_message(text, buttons)
    return len(items)


def _handle_queue_action(
    conn: sqlite3.Connection, telegram: TelegramClient, callback: dict, action: str, queue_id_raw: str
) -> None:
    if not queue_id_raw.isdigit():
        return
    queue_id = int(queue_id_raw)

    if action == ACTION_QUEUE_BUMP:
        repo.bump_to_front(conn, queue_id)
        telegram.answer_callback_query(callback["id"], "대기열 맨 앞으로 옮겼습니다.")
    elif action == ACTION_QUEUE_REMOVE:
        repo.cancel_queue_item(conn, queue_id)
        telegram.answer_callback_query(callback["id"], "대기열에서 제거했습니다.")


def process_pending_reviews(
    conn: sqlite3.Connection,
    telegram: TelegramClient,
    image_backend: ImageBackend,
    storage_client: S3LikeClient,
    last_update_id: int | None = None,
    cfg: AppConfig | None = None,
) -> int:
    """콜백 큐를 처리하고, 다음 폴링에 쓸 update_id 오프셋을 반환한다."""
    cfg = cfg or get_config()
    updates: list[dict[str, Any]] = telegram.get_updates(offset=last_update_id)

    next_offset = last_update_id or 0
    for update in updates:
        next_offset = max(next_offset, update["update_id"] + 1)

        message = update.get("message")
        if message and message.get("text", "").strip() == QUEUE_COMMAND:
            send_queue_status(conn, telegram)
            continue

        callback = update.get("callback_query")
        if not callback:
            continue

        action, _, payload = callback.get("data", "").partition(":")

        if action in (ACTION_QUEUE_BUMP, ACTION_QUEUE_REMOVE):
            _handle_queue_action(conn, telegram, callback, action, payload)
            continue

        draft_id_raw = payload
        if not draft_id_raw.isdigit():
            continue
        draft_id = int(draft_id_raw)

        draft = repo.get_draft(conn, draft_id)
        if draft is None or draft.status != c.DRAFT_PENDING:
            telegram.answer_callback_query(callback["id"], "이미 처리된 초안입니다.")
            continue

        if action == ACTION_DISCARD:
            repo.discard_draft(conn, draft_id)
            telegram.answer_callback_query(callback["id"], "폐기했습니다.")
        elif action == ACTION_APPROVE:
            _approve_and_enqueue(conn, draft_id, image_backend, storage_client, cfg, priority=None)
            telegram.answer_callback_query(callback["id"], "채택 완료 - 대기열에 추가했습니다.")
        elif action == ACTION_APPROVE_TOP:
            _approve_and_enqueue(conn, draft_id, image_backend, storage_client, cfg, priority=0)
            telegram.answer_callback_query(callback["id"], "최우선으로 채택했습니다.")

    return next_offset
