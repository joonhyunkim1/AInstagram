"""대기열에서 다음 게시물을 꺼내 실제로 발행하고 이력에 남긴다."""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

from .. import repository as repo
from ..config import AppConfig, get_config
from ..content.caption import compose_caption
from ..content.llm_client import LLMClient
from ..content.topic_generator import LLM, generate_and_store_drafts, pick_next_category
from ..images import composer, template
from ..images.ai_background import ImageBackend
from ..images.storage import S3LikeClient, upload_image
from ..models import QueueItem
from ..review.telegram_client import TelegramClient
from .instagram_client import InstagramAPIError, InstagramClient


def build_publish_caption(topic: str, caption: str) -> str:
    """인스타그램에 올릴 본문: 제목, 빈 줄, 기존 캡션 순서.

    2,200자 제한을 넘으면 제목은 그대로 두고 본문을 문장 단위로 줄인다. 초안 생성 때도
    줄여두지만, 검수 중에 직접 수정한 캡션이나 그 전에 대기열에 들어간 항목도 있으므로
    발행 직전에 한 번 더 보장한다.
    """
    return compose_caption(topic, caption)


# 한 항목을 몇 번까지 다시 시도할지. 넘으면 대기열에서 제외해서 뒤 항목이 막히지 않게 한다.
MAX_PUBLISH_ATTEMPTS = 3

# 토큰 만료(190)/세션(102)/권한(10, 200번대)/호출 한도(4, 17, 32, 613) - 항목이 아니라
# 계정 전체에 걸린 문제라서, 항목의 실패 횟수에 넣지 않고 대기열에 그대로 둔다.
_ACCOUNT_ERROR_CODES = {4, 10, 17, 32, 102, 190, 613}
# 항목 내용 자체가 거절된 경우 - 다시 시도해도 똑같이 실패하므로 바로 제외한다.
_ITEM_ERROR_SUBCODES = {2207010}  # 캡션 길이 초과

FAILURE_UNCERTAIN = "uncertain"
FAILURE_ITEM = "item"
FAILURE_ACCOUNT = "account"
FAILURE_RETRY = "retry"


def classify_publish_failure(error: Exception) -> str:
    """발행 실패를 어떻게 처리할지 정한다.

    - uncertain: media_publish 단계에서 실패. 실제로는 올라갔을 수 있어서 (8월 말~9월
      403 사례) 다시 시도하면 중복 게시될 수 있다 -> 바로 제외.
    - item: 인스타그램이 항목 내용 자체를 거절 -> 바로 제외.
    - account: 토큰/권한/호출 한도처럼 계정 전체 문제 -> 항목은 그대로 둔다.
    - retry: 그 밖의 (일시적일 수 있는) 문제 -> MAX_PUBLISH_ATTEMPTS번까지 다시 시도.
    """
    if isinstance(error, InstagramAPIError):
        if error.path.endswith("media_publish"):
            return FAILURE_UNCERTAIN
        if error.code in _ACCOUNT_ERROR_CODES or (
            isinstance(error.code, int) and 200 <= error.code < 300
        ):
            return FAILURE_ACCOUNT
        if error.subcode in _ITEM_ERROR_SUBCODES:
            return FAILURE_ITEM
    return FAILURE_RETRY


def _redact(text: str) -> str:
    """requests 연결 에러 등은 토큰이 담긴 URL을 메시지에 넣으므로, 텔레그램이나
    (공개 저장소에 커밋되는) DB에 남기기 전에 토큰을 가린다."""
    return re.sub(r"(access_token=)[^&\s'\"]+", r"\1***", text)


def _handle_publish_failure(
    conn: sqlite3.Connection,
    item: QueueItem,
    topic: str,
    error: Exception,
    telegram: TelegramClient | None,
) -> None:
    """실패한 항목이 매 게시 시각마다 같은 실패를 반복하지 않도록 기록하고, 필요하면
    대기열에서 제외한다. 워크플로우는 실패해도 DB를 커밋하므로 여기서 남긴 상태가 유지된다."""
    message = _redact(str(error))
    kind = classify_publish_failure(error)
    if kind == FAILURE_ACCOUNT:
        note = "토큰/권한/호출 한도 문제로 보여 항목은 대기열에 그대로 둡니다."
    else:
        attempts = repo.record_publish_failure(conn, item.id, message)
        if kind == FAILURE_UNCERTAIN:
            repo.mark_queue_item_failed(conn, item.id)
            note = (
                "발행 요청 단계에서 실패해 실제로는 게시됐을 수도 있습니다. 중복 게시를 막기 위해 "
                "다시 시도하지 않고 대기열에서 제외했습니다 - 피드를 확인해주세요."
            )
        elif kind == FAILURE_ITEM:
            repo.mark_queue_item_failed(conn, item.id)
            note = "인스타그램이 게시물 내용을 거절해 대기열에서 제외했습니다."
        elif attempts >= MAX_PUBLISH_ATTEMPTS:
            repo.mark_queue_item_failed(conn, item.id)
            note = f"{attempts}회 연속 실패해 대기열에서 제외했습니다."
        else:
            note = f"다음 게시 시각에 다시 시도합니다 ({attempts}/{MAX_PUBLISH_ATTEMPTS}회 실패)."

    if telegram is not None:
        telegram.send_message(f"❌ 게시 실패: {topic}\n\n{message[:1000]}\n\n→ {note}")


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
        _handle_publish_failure(conn, item, topic, e, telegram)
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
