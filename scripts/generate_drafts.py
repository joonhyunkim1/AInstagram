"""매일 1회 실행: 카테고리 하나를 골라 예비 게시물 후보를 생성하고 Telegram으로 검수 요청을 보낸다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram.config import get_config
from ainstagram.content.llm_client import LLMClient
from ainstagram.content.topic_generator import generate_and_store_drafts, pick_next_category
from ainstagram.db import get_connection
from ainstagram.images.ai_background import OpenAIImageBackend
from ainstagram.images.storage import get_r2_client
from ainstagram.review.bot import send_drafts_for_review
from ainstagram.review.telegram_client import TelegramClient


def main() -> None:
    cfg = get_config()
    conn = get_connection()

    category = pick_next_category(conn, cfg)
    llm = LLMClient.from_config()
    draft_ids = generate_and_store_drafts(conn, category, llm)
    print(f"생성된 초안: {draft_ids} (카테고리: {category})")

    telegram = TelegramClient.from_env()
    image_backend = OpenAIImageBackend.from_config()
    storage_client = get_r2_client()
    sent = send_drafts_for_review(conn, telegram, image_backend, storage_client, cfg)
    print(f"검수 요청 전송: {sent}건")


if __name__ == "__main__":
    main()
