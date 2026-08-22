"""주기적으로 실행: Telegram 검수 버튼(채택/최우선채택/폐기) 콜백을 처리한다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram import repository as repo
from ainstagram.db import get_connection
from ainstagram.images.ai_background import OpenAIImageBackend
from ainstagram.images.storage import get_r2_client
from ainstagram.review.bot import process_pending_reviews
from ainstagram.review.telegram_client import TelegramClient

OFFSET_KEY = "telegram_update_offset"


def main() -> None:
    conn = get_connection()
    telegram = TelegramClient.from_env()
    image_backend = OpenAIImageBackend.from_config()
    storage_client = get_r2_client()

    stored_offset = repo.get_state(conn, OFFSET_KEY)
    offset = int(stored_offset) if stored_offset else None

    next_offset = process_pending_reviews(conn, telegram, image_backend, storage_client, offset)
    repo.set_state(conn, OFFSET_KEY, str(next_offset))
    print(f"다음 폴링 offset: {next_offset}")


if __name__ == "__main__":
    main()
