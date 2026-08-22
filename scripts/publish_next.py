"""정해진 시각마다 실행: 대기열의 다음 게시물을 인스타그램에 발행한다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram.db import get_connection
from ainstagram.publish.instagram_client import InstagramClient
from ainstagram.publish.queue_worker import publish_next


def main() -> None:
    conn = get_connection()
    instagram = InstagramClient.from_env()
    media_id = publish_next(conn, instagram)
    if media_id:
        print(f"발행 완료: {media_id}")
    else:
        print("대기열이 비어있어 발행하지 않음")


if __name__ == "__main__":
    main()
