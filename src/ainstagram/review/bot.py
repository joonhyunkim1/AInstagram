"""초안 검수 + 대기열 관리 + 게시물별 수정 흐름.

1) send_drafts_for_review: pending 상태인 초안들의 슬라이드 전체(실제 게시될 최종 품질)를
   렌더링해서 Telegram 앨범으로 전송. 이때 만든 이미지는 draft에 저장해둔다.
2) process_pending_reviews: getUpdates를 폴링해서 버튼 응답(채택/우선채택/수정/폐기),
   /queue 명령어, 수정 메뉴(이미지 삭제/추가, 캡션 수정)를 처리
   - 채택 시 draft에 저장된 이미지를 그대로 재사용해서 대기열에 넣는다 (다시 만들지 않음)
   - 이미지 추가/캡션 수정은 버튼 클릭 후 다음 메시지를 답변으로 받아야 해서, kv_state에
     '지금 무엇을 기다리는 중인지'를 저장해두고 다음 업데이트에서 그걸 소비한다
"""
from __future__ import annotations

import io
import json
import sqlite3
from typing import Any

from PIL import Image

from .. import constants as c
from .. import repository as repo
from ..config import AppConfig, get_config
from ..content.llm_client import LLMClient
from ..content.topic_generator import LLM, generate_and_store_drafts, pick_next_category
from ..images import composer, template
from ..images.ai_background import ImageBackend
from ..images.storage import S3LikeClient, upload_image
from ..models import Draft
from .telegram_client import TelegramClient

ACTION_APPROVE = "approve"
ACTION_APPROVE_TOP = "approve_top"
ACTION_DISCARD = "discard"
ACTION_QUEUE_BUMP = "queue_bump"
ACTION_QUEUE_REMOVE = "queue_remove"

ACTION_EDIT_MENU = "edit_menu"
ACTION_DELETE_IMAGE_MENU = "del_img_menu"
ACTION_DELETE_IMAGE = "del_img"
ACTION_ADD_IMAGE_AI = "add_img_ai"
ACTION_ADD_IMAGE_UPLOAD = "add_img_upload"
ACTION_EDIT_CAPTION = "edit_caption"
ACTION_EDIT_CANCEL = "edit_cancel"

QUEUE_COMMAND = "/queue"
GENERATE_COMMAND = "/generate"
PENDING_ACTION_STATE_KEY = "pending_edit_action"


def _get_pending_action(conn: sqlite3.Connection) -> dict | None:
    raw = repo.get_state(conn, PENDING_ACTION_STATE_KEY)
    return json.loads(raw) if raw else None


def _set_pending_action(conn: sqlite3.Connection, action: dict | None) -> None:
    repo.set_state(conn, PENDING_ACTION_STATE_KEY, json.dumps(action) if action else "")


def _is_editable(draft: Draft | None) -> bool:
    return draft is not None and draft.status == c.DRAFT_PENDING


def _render_and_store_images(
    conn: sqlite3.Connection,
    draft: Draft,
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


def _send_draft_preview(
    telegram: TelegramClient, draft: Draft, media: list[bytes | str] | None = None
) -> None:
    """초안의 현재 상태(슬라이드 전체 + 캡션 + 채택/수정/폐기 버튼)를 전송한다.

    media를 안 넘기면 draft.image_urls(R2 공개 URL)로 보낸다. 방금 막 R2에 올린 이미지는
    Telegram이 아직 못 가져올 때가 있어서(WEBPAGE_CURL_FAILED), 막 만든 이미지가 섞여있는
    호출부는 그 이미지의 바이트를 직접 media로 넘긴다.
    """
    telegram.send_media_group(media if media is not None else (draft.image_urls or []))
    caption = f"[{draft.category}] {draft.topic}\n\n{draft.caption}\n\n{len(draft.slides)} slides"
    buttons = [
        [
            {"text": "✅ 채택", "callback_data": f"{ACTION_APPROVE}:{draft.id}"},
            {"text": "⬆️ 최우선 채택", "callback_data": f"{ACTION_APPROVE_TOP}:{draft.id}"},
        ],
        [
            {"text": "✏️ 수정", "callback_data": f"{ACTION_EDIT_MENU}:{draft.id}"},
            {"text": "❌ 폐기", "callback_data": f"{ACTION_DISCARD}:{draft.id}"},
        ],
    ]
    telegram.send_message(caption, buttons)


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

    for draft in pending:
        images = composer.compose_slides(
            draft.topic, draft.category, draft.slides, image_backend, cfg.image.quality.final, style
        )
        image_urls = [
            upload_image(storage_client, image, f"posts/{draft.id}/{i}.jpg")
            for i, image in enumerate(images)
        ]
        repo.set_draft_images(conn, draft.id, image_urls)
        draft.image_urls = image_urls

        image_bytes_list = []
        for image in images:
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=90)
            image_bytes_list.append(buf.getvalue())
        # 막 렌더링한 이미지라 R2 URL이 아니라 바이트를 직접 첨부한다 (Telegram이
        # 외부 fetch를 아예 안 해도 되게)
        _send_draft_preview(telegram, draft, media=image_bytes_list)
    return len(pending)


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


def _generate_new_drafts(
    conn: sqlite3.Connection,
    telegram: TelegramClient,
    image_backend: ImageBackend,
    storage_client: S3LikeClient,
    cfg: AppConfig,
    llm: LLM | None = None,
) -> int:
    """/generate 명령으로 예비 게시물 후보를 즉시 생성해서 검수 요청을 보낸다."""
    llm = llm or LLMClient.from_config()
    category = pick_next_category(conn, cfg)
    generate_and_store_drafts(conn, category, llm)
    return send_drafts_for_review(conn, telegram, image_backend, storage_client, cfg)


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
            [
                {"text": "⬆️ 최우선으로", "callback_data": f"{ACTION_QUEUE_BUMP}:{item.id}"},
                {"text": "❌ 대기열에서 제거", "callback_data": f"{ACTION_QUEUE_REMOVE}:{item.id}"},
            ]
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


# ---- 수정 메뉴 ----

def _send_edit_menu(telegram: TelegramClient, draft_id: int) -> None:
    buttons = [
        [
            {"text": "🗑️ 이미지 삭제", "callback_data": f"{ACTION_DELETE_IMAGE_MENU}:{draft_id}"},
            {"text": "➕ AI로 이미지 추가", "callback_data": f"{ACTION_ADD_IMAGE_AI}:{draft_id}"},
        ],
        [
            {"text": "➕ 사진 업로드로 추가", "callback_data": f"{ACTION_ADD_IMAGE_UPLOAD}:{draft_id}"},
            {"text": "📝 캡션 수정", "callback_data": f"{ACTION_EDIT_CAPTION}:{draft_id}"},
        ],
        [{"text": "↩️ 취소", "callback_data": f"{ACTION_EDIT_CANCEL}:{draft_id}"}],
    ]
    telegram.send_message("무엇을 수정할까요?", buttons)


def _send_delete_image_menu(telegram: TelegramClient, draft: Draft) -> None:
    image_urls = draft.image_urls or []
    if len(image_urls) <= 1:
        telegram.send_message("이미지가 1장뿐이라 더 삭제할 수 없습니다.")
        return
    number_buttons = [
        {"text": str(i + 1), "callback_data": f"{ACTION_DELETE_IMAGE}:{draft.id}:{i}"}
        for i in range(len(image_urls))
    ]
    rows = [number_buttons[i : i + 4] for i in range(0, len(number_buttons), 4)]
    rows.append([{"text": "↩️ 취소", "callback_data": f"{ACTION_EDIT_CANCEL}:{draft.id}"}])
    telegram.send_message("삭제할 슬라이드 번호를 선택하세요.", rows)


def _delete_image(
    conn: sqlite3.Connection, telegram: TelegramClient, callback: dict, draft: Draft, index: int
) -> None:
    image_urls = draft.image_urls or []
    if len(image_urls) <= 1:
        telegram.answer_callback_query(callback["id"], "이미지가 1장뿐이라 삭제할 수 없습니다.")
        return
    if index < 0 or index >= len(image_urls):
        telegram.answer_callback_query(callback["id"], "잘못된 슬라이드 번호입니다.")
        return

    slides = list(draft.slides)
    new_image_urls = list(image_urls)
    del slides[index]
    del new_image_urls[index]
    repo.set_draft_slides(conn, draft.id, slides)
    repo.set_draft_images(conn, draft.id, new_image_urls)
    telegram.answer_callback_query(callback["id"], f"{index + 1}번 슬라이드를 삭제했습니다.")

    draft.slides = slides
    draft.image_urls = new_image_urls
    _send_draft_preview(telegram, draft)


def _prompt_add_image_ai(conn: sqlite3.Connection, telegram: TelegramClient, draft_id: int) -> None:
    _set_pending_action(conn, {"type": ACTION_ADD_IMAGE_AI, "draft_id": draft_id})
    telegram.send_message("추가할 슬라이드 문구를 입력해주세요 (3문장 정도).")


def _prompt_add_image_upload(conn: sqlite3.Connection, telegram: TelegramClient, draft_id: int) -> None:
    _set_pending_action(conn, {"type": ACTION_ADD_IMAGE_UPLOAD, "draft_id": draft_id})
    telegram.send_message("추가할 사진을 보내주세요.")


def _prompt_edit_caption(conn: sqlite3.Connection, telegram: TelegramClient, draft_id: int) -> None:
    _set_pending_action(conn, {"type": ACTION_EDIT_CAPTION, "draft_id": draft_id})
    telegram.send_message("새 캡션을 입력해주세요.")


def _complete_add_image_ai(
    conn: sqlite3.Connection,
    telegram: TelegramClient,
    image_backend: ImageBackend,
    storage_client: S3LikeClient,
    cfg: AppConfig,
    draft_id: int,
    slide_text: str,
) -> None:
    draft = repo.get_draft(conn, draft_id)
    if not _is_editable(draft):
        telegram.send_message("이미 처리된 초안이라 수정할 수 없습니다.")
        return

    style = template.load_brand_style(cfg)
    label = composer.CATEGORY_LABELS.get(draft.category, draft.category.upper())
    new_index = len(draft.slides)
    image = composer.compose_single_slide(
        draft.topic,
        slide_text,
        new_index,
        new_index + 1,
        False,
        image_backend,
        cfg.image.quality.final,
        style,
        label,
    )
    url = upload_image(storage_client, image, f"posts/{draft.id}/{new_index}.jpg")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    new_image_bytes = buf.getvalue()

    slides = list(draft.slides) + [slide_text]
    image_urls = list(draft.image_urls or []) + [url]
    repo.set_draft_slides(conn, draft.id, slides)
    repo.set_draft_images(conn, draft.id, image_urls)

    draft.slides = slides
    draft.image_urls = image_urls
    telegram.send_message("이미지를 추가했습니다.")
    # 기존 이미지는 URL로, 방금 만든 이미지는 바이트로 직접 첨부 (막 올려서 Telegram이
    # 아직 못 가져올 수 있어서)
    media: list[bytes | str] = list(image_urls[:-1]) + [new_image_bytes]
    _send_draft_preview(telegram, draft, media=media)


def _complete_add_image_upload(
    conn: sqlite3.Connection,
    telegram: TelegramClient,
    storage_client: S3LikeClient,
    draft_id: int,
    file_id: str,
) -> None:
    draft = repo.get_draft(conn, draft_id)
    if not _is_editable(draft):
        telegram.send_message("이미 처리된 초안이라 수정할 수 없습니다.")
        return

    file_bytes = telegram.download_file(file_id)
    image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    new_index = len(draft.slides)
    url = upload_image(storage_client, image, f"posts/{draft.id}/{new_index}.jpg")

    slides = list(draft.slides) + [""]
    image_urls = list(draft.image_urls or []) + [url]
    repo.set_draft_slides(conn, draft.id, slides)
    repo.set_draft_images(conn, draft.id, image_urls)

    draft.slides = slides
    draft.image_urls = image_urls
    telegram.send_message("사진을 추가했습니다.")
    media: list[bytes | str] = list(image_urls[:-1]) + [file_bytes]
    _send_draft_preview(telegram, draft, media=media)


def _complete_edit_caption(
    conn: sqlite3.Connection, telegram: TelegramClient, draft_id: int, new_caption: str
) -> None:
    draft = repo.get_draft(conn, draft_id)
    if not _is_editable(draft):
        telegram.send_message("이미 처리된 초안이라 수정할 수 없습니다.")
        return

    repo.set_draft_caption(conn, draft.id, new_caption)
    draft.caption = new_caption
    telegram.send_message("캡션을 수정했습니다.")
    _send_draft_preview(telegram, draft)


def _handle_pending_action(
    conn: sqlite3.Connection,
    telegram: TelegramClient,
    image_backend: ImageBackend,
    storage_client: S3LikeClient,
    cfg: AppConfig,
    pending: dict,
    message: dict,
) -> None:
    pending_type = pending.get("type")
    draft_id = pending.get("draft_id")

    if pending_type == ACTION_ADD_IMAGE_AI and message.get("text"):
        _complete_add_image_ai(
            conn, telegram, image_backend, storage_client, cfg, draft_id, message["text"].strip()
        )
    elif pending_type == ACTION_ADD_IMAGE_UPLOAD and message.get("photo"):
        file_id = message["photo"][-1]["file_id"]
        _complete_add_image_upload(conn, telegram, storage_client, draft_id, file_id)
    elif pending_type == ACTION_EDIT_CAPTION and message.get("text"):
        _complete_edit_caption(conn, telegram, draft_id, message["text"].strip())
    else:
        telegram.send_message("예상한 형식이 아니라서 취소했습니다. 버튼을 다시 눌러주세요.")


_EDIT_MENU_ACTIONS = {
    ACTION_EDIT_MENU,
    ACTION_DELETE_IMAGE_MENU,
    ACTION_ADD_IMAGE_AI,
    ACTION_ADD_IMAGE_UPLOAD,
    ACTION_EDIT_CAPTION,
    ACTION_EDIT_CANCEL,
}


def _handle_edit_menu_action(
    conn: sqlite3.Connection,
    telegram: TelegramClient,
    callback: dict,
    action: str,
    draft_id: int,
) -> None:
    draft = repo.get_draft(conn, draft_id)
    if not _is_editable(draft):
        telegram.answer_callback_query(callback["id"], "이미 처리된 초안이라 수정할 수 없습니다.")
        return

    telegram.answer_callback_query(callback["id"], "")
    if action == ACTION_EDIT_MENU:
        _send_edit_menu(telegram, draft_id)
    elif action == ACTION_DELETE_IMAGE_MENU:
        _send_delete_image_menu(telegram, draft)
    elif action == ACTION_ADD_IMAGE_AI:
        _prompt_add_image_ai(conn, telegram, draft_id)
    elif action == ACTION_ADD_IMAGE_UPLOAD:
        _prompt_add_image_upload(conn, telegram, draft_id)
    elif action == ACTION_EDIT_CAPTION:
        _prompt_edit_caption(conn, telegram, draft_id)
    elif action == ACTION_EDIT_CANCEL:
        _set_pending_action(conn, None)


def process_updates(
    conn: sqlite3.Connection,
    updates: list[dict[str, Any]],
    telegram: TelegramClient,
    image_backend: ImageBackend,
    storage_client: S3LikeClient,
    cfg: AppConfig | None = None,
    llm: LLM | None = None,
) -> None:
    """이미 확보한 업데이트 목록을 처리한다.

    getUpdates 폴링(process_pending_reviews)뿐 아니라, Telegram 웹훅으로 즉시 들어온
    업데이트 하나를 처리하는 경로(scripts/poll_reviews.py의 TELEGRAM_UPDATE_JSON)에서도
    이 함수를 그대로 재사용한다.
    """
    cfg = cfg or get_config()

    for update in updates:
        message = update.get("message")
        if message:
            pending = _get_pending_action(conn)
            if pending is not None:
                _set_pending_action(conn, None)
                _handle_pending_action(
                    conn, telegram, image_backend, storage_client, cfg, pending, message
                )
                continue
            text = message.get("text", "").strip()
            if text == QUEUE_COMMAND:
                send_queue_status(conn, telegram)
            elif text == GENERATE_COMMAND:
                telegram.send_message("예비 게시물 3건을 생성하고 있습니다...")
                sent = _generate_new_drafts(conn, telegram, image_backend, storage_client, cfg, llm)
                if sent == 0:
                    telegram.send_message("생성된 초안이 없습니다 (중복으로 모두 걸러졌을 수 있습니다).")
            continue

        callback = update.get("callback_query")
        if not callback:
            continue

        action, _, payload = callback.get("data", "").partition(":")

        if action in (ACTION_QUEUE_BUMP, ACTION_QUEUE_REMOVE):
            _handle_queue_action(conn, telegram, callback, action, payload)
            continue

        if action == ACTION_DELETE_IMAGE:
            draft_id_raw, _, index_raw = payload.partition(":")
            if not (draft_id_raw.isdigit() and index_raw.isdigit()):
                continue
            draft = repo.get_draft(conn, int(draft_id_raw))
            if not _is_editable(draft):
                telegram.answer_callback_query(callback["id"], "이미 처리된 초안이라 수정할 수 없습니다.")
                continue
            _delete_image(conn, telegram, callback, draft, int(index_raw))
            continue

        if action in _EDIT_MENU_ACTIONS:
            if not payload.isdigit():
                continue
            _handle_edit_menu_action(conn, telegram, callback, action, int(payload))
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
            telegram.send_message(f"❌ 폐기했습니다: {draft.topic}")
        elif action == ACTION_APPROVE:
            _approve_and_enqueue(conn, draft_id, image_backend, storage_client, cfg, priority=None)
            telegram.answer_callback_query(callback["id"], "채택 완료 - 대기열에 추가했습니다.")
        elif action == ACTION_APPROVE_TOP:
            _approve_and_enqueue(conn, draft_id, image_backend, storage_client, cfg, priority=0)
            telegram.answer_callback_query(callback["id"], "최우선으로 채택했습니다.")


def process_pending_reviews(
    conn: sqlite3.Connection,
    telegram: TelegramClient,
    image_backend: ImageBackend,
    storage_client: S3LikeClient,
    last_update_id: int | None = None,
    cfg: AppConfig | None = None,
    llm: LLM | None = None,
) -> int:
    """getUpdates로 폴링해서 처리하고, 다음 폴링에 쓸 update_id 오프셋을 반환한다.

    Telegram 웹훅이 활성화되어 있으면 get_updates는 항상 빈 리스트를 반환하므로
    (TelegramClient가 409를 조용히 무시함), 이 경우 이 함수는 사실상 아무 일도 안 한다.
    """
    cfg = cfg or get_config()
    updates: list[dict[str, Any]] = telegram.get_updates(offset=last_update_id)

    next_offset = last_update_id or 0
    for update in updates:
        next_offset = max(next_offset, update["update_id"] + 1)

    process_updates(conn, updates, telegram, image_backend, storage_client, cfg, llm)
    return next_offset
