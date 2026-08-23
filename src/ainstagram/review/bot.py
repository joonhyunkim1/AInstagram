"""초안 검수 + 대기열 관리 흐름.

1) send_drafts_for_review: pending 상태인 초안들의 썸네일 미리보기(저품질)를 Telegram으로 전송
2) process_pending_reviews: getUpdates를 폴링해서 버튼 응답(채택/우선채택/폐기) 및
   /queue 명령어(대기열 조회/최우선으로/제거)를 처리
   - 채택 시에만 그 초안을 실제 품질로 풀세트 렌더링해서 R2에 올리고 대기열에 넣는다
     (검수 단계에서는 후보 3개 다 풀세트로 만들지 않아 이미지 생성 비용을 아낀다)
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


def send_drafts_for_review(
    conn: sqlite3.Connection,
    telegram: TelegramClient,
    image_backend: ImageBackend,
    cfg: AppConfig | None = None,
) -> int:
    """pending 초안들의 미리보기를 전송한다. 전송한 개수를 반환."""
    cfg = cfg or get_config()
    style = template.load_brand_style(cfg)
    pending = repo.list_pending_drafts(conn)

    sent = 0
    for draft in pending:
        hook_text = draft.slides[0] if draft.slides else draft.topic
        prompt = composer.build_background_prompt(draft.topic, hook_text, is_thumbnail=True)
        background = image_backend.generate_background(prompt, cfg.image.quality.draft_preview)
        label = composer.CATEGORY_LABELS.get(draft.category, draft.category.upper())
        preview = template.render_thumbnail(background, draft.topic, label, style)

        buf = io.BytesIO()
        preview.save(buf, format="JPEG", quality=85)

        caption = f"[{draft.category}] {draft.topic}\n\n{draft.caption}\n\n슬라이드 {len(draft.slides)}장"
        buttons = [
            {"text": "✅ 채택", "callback_data": f"{ACTION_APPROVE}:{draft.id}"},
            {"text": "⬆️ 최우선 채택", "callback_data": f"{ACTION_APPROVE_TOP}:{draft.id}"},
            {"text": "❌ 폐기", "callback_data": f"{ACTION_DISCARD}:{draft.id}"},
        ]
        telegram.send_photo_with_buttons(buf.getvalue(), caption, buttons)
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

    style = template.load_brand_style(cfg)
    images = composer.compose_slides(
        draft.topic, draft.category, draft.slides, image_backend, cfg.image.quality.final, style
    )
    image_urls = [
        upload_image(storage_client, image, f"posts/{draft_id}/{i}.jpg")
        for i, image in enumerate(images)
    ]

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
