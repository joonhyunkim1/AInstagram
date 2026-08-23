"""Telegram 검수 버튼/메시지를 처리한다.

두 가지 방식으로 실행된다:
1) 웹훅(Cloudflare Worker)이 TELEGRAM_UPDATE_JSON 환경변수로 업데이트 하나를 바로 넘겨주는 경우
   -> 그 업데이트 하나만 즉시 처리 (getUpdates 폴링 없음, offset도 안 건드림)
2) 위 환경변수가 없는 경우 (기존 스케줄 폴링 / 수동 실행)
   -> getUpdates로 폴링. 웹훅이 활성화되어 있으면 항상 빈 리스트가 와서 아무 일도 안 한다
      (TelegramClient가 409 Conflict를 조용히 무시함).
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram import repository as repo
from ainstagram.db import get_connection
from ainstagram.images.ai_background import OpenAIImageBackend
from ainstagram.images.storage import get_r2_client
from ainstagram.review.bot import process_pending_reviews, process_updates
from ainstagram.review.telegram_client import TelegramClient

OFFSET_KEY = "telegram_update_offset"


def main() -> None:
    conn = get_connection()
    telegram = TelegramClient.from_env()
    image_backend = OpenAIImageBackend.from_config()
    storage_client = get_r2_client()

    raw_update = os.getenv("TELEGRAM_UPDATE_JSON", "").strip()
    if raw_update:
        update = json.loads(raw_update)
        process_updates(conn, [update], telegram, image_backend, storage_client)
        print(f"웹훅 업데이트 처리 완료: update_id={update.get('update_id')}")
        return

    stored_offset = repo.get_state(conn, OFFSET_KEY)
    offset = int(stored_offset) if stored_offset else None

    next_offset = process_pending_reviews(conn, telegram, image_backend, storage_client, offset)
    repo.set_state(conn, OFFSET_KEY, str(next_offset))
    print(f"다음 폴링 offset: {next_offset}")


if __name__ == "__main__":
    main()
