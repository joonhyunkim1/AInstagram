"""인스타그램 피드를 기준으로 누락된 발행 이력을 복구한다 (필요할 때 수동 실행).

사용법: python scripts/backfill_history.py [--dry-run]
--dry-run이면 DB를 바꾸지 않고 복구 대상만 출력한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram.config import get_config
from ainstagram.content.llm_client import LLMClient
from ainstagram.content.topic_generator import pick_next_category
from ainstagram.db import get_connection
from ainstagram.publish.history_backfill import backfill_history
from ainstagram.publish.instagram_client import InstagramClient


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    cfg = get_config()
    conn = get_connection()
    instagram = InstagramClient.from_env()

    category = pick_next_category(conn, cfg)
    embed = None if dry_run else LLMClient.from_config().embed
    restored = backfill_history(conn, instagram.iter_media(), embed, category, dry_run=dry_run)

    label = "복구 대상" if dry_run else "복구 완료"
    print(f"{label}: {len(restored)}건 (카테고리: {category})")
    for item in restored:
        print(f"  {item['published_at'][:19]} {item['id']} {item['topic'][:70]}")


if __name__ == "__main__":
    main()
