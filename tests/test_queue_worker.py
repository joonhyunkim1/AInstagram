import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image

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


class FakeImageBackend:
    def generate_background(self, prompt, quality, size="1024x1024"):
        return Image.new("RGB", (64, 64), color=(100, 100, 100))


class FakeS3Client:
    def put_object(self, **kwargs):
        pass


class FakeLLM:
    def generate_topics(self, category, context, count):
        return [{"topic": "새로 생성된 주제", "caption": "캡션", "slides": ["표지", "본문1"]} for _ in range(count)]

    def embed(self, text):
        return [0.0, 0.0]


class FakeTelegram:
    def __init__(self):
        self.messages = []

    def send_message(self, text, buttons=None):
        self.messages.append(text)


class FailingInstagram:
    def __init__(self, error=RuntimeError("Instagram API 오류: rate limited")):
        self.error = error
        self.calls = []

    def publish_carousel(self, image_urls, caption):
        self.calls.append((image_urls, caption))
        raise self.error


def test_publish_next_returns_none_when_queue_empty(tmp_path):
    conn = make_conn(tmp_path)
    assert queue_worker.publish_next(conn, FakeInstagram()) is None


def test_publish_next_auto_generates_and_publishes_when_queue_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.example.com")
    conn = make_conn(tmp_path)
    instagram = FakeInstagram(media_id="media-auto")
    telegram = FakeTelegram()

    media_id = queue_worker.publish_next(
        conn,
        instagram,
        image_backend=FakeImageBackend(),
        storage_client=FakeS3Client(),
        telegram=telegram,
        llm=FakeLLM(),
    )

    assert media_id == "media-auto"
    assert len(instagram.calls) == 1
    assert repo.next_in_queue(conn) is None
    history = repo.recent_history(conn)
    assert len(history) == 1
    assert history[0].topic == "새로 생성된 주제"
    assert len(telegram.messages) == 1
    assert "검수 없이" in telegram.messages[0]


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
    assert instagram.calls[0] == (["https://cdn.example.com/1.jpg"], "트랜스포머 기초\n\n캡션")

    # 발행된 큐 항목은 다음 조회에서 빠져야 함
    assert repo.next_in_queue(conn) is None

    history = repo.recent_history(conn, category=c.CATEGORY_KNOWLEDGE)
    assert len(history) == 1
    assert history[0].topic == "트랜스포머 기초"
    assert history[0].instagram_media_id == "media-42"
    assert history[0].difficulty_level == 2
    assert history[0].embedding == [0.1, 0.2]


def test_publish_next_sends_telegram_success_message(tmp_path):
    conn = make_conn(tmp_path)
    draft_id = repo.create_draft(
        conn, category=c.CATEGORY_NEWS, topic="주제A", caption="캡션", slides=["s1"]
    )
    repo.approve_draft(conn, draft_id, priority=50)
    repo.enqueue(conn, draft_id, "캡션", ["https://cdn.example.com/1.jpg"], priority=50)

    telegram = FakeTelegram()
    media_id = queue_worker.publish_next(conn, FakeInstagram(media_id="media-99"), telegram=telegram)

    assert media_id == "media-99"
    assert len(telegram.messages) == 1
    assert "✅ 게시 완료" in telegram.messages[0]
    assert "주제A" in telegram.messages[0]


def test_publish_next_sends_telegram_failure_message_and_reraises(tmp_path):
    conn = make_conn(tmp_path)
    draft_id = repo.create_draft(
        conn, category=c.CATEGORY_NEWS, topic="주제B", caption="캡션", slides=["s1"]
    )
    repo.approve_draft(conn, draft_id, priority=50)
    queue_id = repo.enqueue(conn, draft_id, "캡션", ["https://cdn.example.com/1.jpg"], priority=50)

    telegram = FakeTelegram()
    instagram = FailingInstagram()

    try:
        queue_worker.publish_next(conn, instagram, telegram=telegram)
        assert False, "예외가 발생해야 함"
    except RuntimeError:
        pass

    assert len(telegram.messages) == 1
    assert "❌ 게시 실패" in telegram.messages[0]
    assert "주제B" in telegram.messages[0]

    # 발행에 실패했으니 큐/이력 상태는 그대로 유지되어야 함
    remaining = repo.next_in_queue(conn)
    assert remaining is not None
    assert remaining.id == queue_id
    assert repo.recent_history(conn) == []


def test_build_publish_caption_puts_title_then_blank_line_then_caption():
    assert queue_worker.build_publish_caption("제목", "본문 #tag") == "제목\n\n본문 #tag"
    assert queue_worker.build_publish_caption("  제목  ", "본문") == "제목\n\n본문"


def test_build_publish_caption_without_topic_returns_caption_only():
    assert queue_worker.build_publish_caption("", "본문") == "본문"
