import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram import constants as c
from ainstagram import repository as repo
from ainstagram.db import get_connection
from ainstagram.publish import queue_worker


def make_conn(tmp_path):
    return get_connection(tmp_path / "test.db")


class FakeInstagram:
    def __init__(self, media_id="media-1"):
        self.media_id = media_id
        self.calls = []

    def publish_carousel(self, image_urls, caption):
        self.calls.append((image_urls, caption))
        return self.media_id


def test_publish_next_returns_none_when_queue_empty(tmp_path):
    conn = make_conn(tmp_path)
    assert queue_worker.publish_next(conn, FakeInstagram()) is None


def test_publish_next_publishes_and_records_history(tmp_path):
    conn = make_conn(tmp_path)
    draft_id = repo.create_draft(
        conn,
        category=c.CATEGORY_KNOWLEDGE,
        topic="트랜스포머 기초",
        caption="캡션",
        slides=["s1", "s2"],
        embedding=[0.1, 0.2],
        difficulty_level=2,
    )
    repo.approve_draft(conn, draft_id, priority=50)
    queue_id = repo.enqueue(conn, draft_id, "캡션", ["https://cdn.example.com/1.jpg"], priority=50)

    instagram = FakeInstagram(media_id="media-42")
    media_id = queue_worker.publish_next(conn, instagram)

    assert media_id == "media-42"
    assert instagram.calls[0] == (["https://cdn.example.com/1.jpg"], "캡션")

    # 발행된 큐 항목은 다음 조회에서 빠져야 함
    assert repo.next_in_queue(conn) is None

    history = repo.recent_history(conn, category=c.CATEGORY_KNOWLEDGE)
    assert len(history) == 1
    assert history[0].topic == "트랜스포머 기초"
    assert history[0].instagram_media_id == "media-42"
    assert history[0].difficulty_level == 2
    assert history[0].embedding == [0.1, 0.2]
