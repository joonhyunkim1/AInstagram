import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PIL import Image

from ainstagram import constants as c
from ainstagram import repository as repo
from ainstagram.content.caption import INSTAGRAM_CAPTION_LIMIT, caption_length
from ainstagram.db import get_connection
from ainstagram.publish import queue_worker
from ainstagram.publish.instagram_client import InstagramAPIError


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


def enqueue_item(conn, topic, caption="캡션", priority=50):
    draft_id = repo.create_draft(
        conn, category=c.CATEGORY_NEWS, topic=topic, caption=caption, slides=["s1"]
    )
    repo.approve_draft(conn, draft_id, priority=priority)
    return repo.enqueue(conn, draft_id, caption, ["https://cdn.example.com/1.jpg"], priority=priority)


def queue_row(conn, queue_id):
    return conn.execute("SELECT * FROM queue WHERE id = ?", (queue_id,)).fetchone()


def caption_too_long_error():
    return InstagramAPIError(
        "Instagram API 오류 400 (IGID/media): The caption was too long.",
        status=400, code=36004, subcode=2207010, path="IGID/media",
    )


def publish_forbidden_error():
    return InstagramAPIError(
        "Instagram API 오류 403 (IGID/media_publish): Application request limit reached",
        status=403, code=4, subcode=2207051, path="IGID/media_publish",
    )


def expired_token_error():
    return InstagramAPIError(
        "Instagram API 오류 400 (IGID/media): Error validating access token",
        status=400, code=190, path="IGID/media",
    )


def run_failing(conn, error, telegram=None):
    with pytest.raises(type(error)):
        queue_worker.publish_next(conn, FailingInstagram(error), telegram=telegram)


def test_publish_next_trims_over_limit_caption_but_keeps_title(tmp_path):
    conn = make_conn(tmp_path)
    body = " ".join(f"Sentence {i} adds another supporting detail to the article." for i in range(60))
    caption = f"{body}\n\n#AI #OpenAI #LLM"
    enqueue_item(conn, "Long news title", caption=caption)
    assert caption_length(f"Long news title\n\n{caption}") > INSTAGRAM_CAPTION_LIMIT

    instagram = FakeInstagram()
    queue_worker.publish_next(conn, instagram)

    sent = instagram.calls[0][1]
    assert caption_length(sent) <= INSTAGRAM_CAPTION_LIMIT
    assert sent.startswith("Long news title\n\nSentence 0 ")
    assert sent.endswith("to the article.\n\n#AI #OpenAI #LLM")


def test_rejected_item_is_removed_from_queue_immediately(tmp_path):
    conn = make_conn(tmp_path)
    bad_id = enqueue_item(conn, "거절될 주제", priority=10)
    next_id = enqueue_item(conn, "다음 주제", priority=20)
    telegram = FakeTelegram()

    run_failing(conn, caption_too_long_error(), telegram)

    row = queue_row(conn, bad_id)
    assert row["status"] == c.QUEUE_FAILED
    assert row["attempts"] == 1
    assert "caption was too long" in row["last_error"]
    assert repo.next_in_queue(conn).id == next_id  # 뒤 항목이 막히지 않음
    assert "대기열에서 제외" in telegram.messages[0]


def test_transient_failure_retries_then_gives_up_after_max_attempts(tmp_path):
    conn = make_conn(tmp_path)
    item_id = enqueue_item(conn, "불안정한 주제", priority=10)
    next_id = enqueue_item(conn, "다음 주제", priority=20)

    for attempt in range(1, queue_worker.MAX_PUBLISH_ATTEMPTS):
        run_failing(conn, RuntimeError("503 Service Unavailable"))
        assert queue_row(conn, item_id)["attempts"] == attempt
        assert repo.next_in_queue(conn).id == item_id  # 아직은 같은 항목을 다시 시도

    run_failing(conn, RuntimeError("503 Service Unavailable"))

    assert queue_row(conn, item_id)["status"] == c.QUEUE_FAILED
    assert repo.next_in_queue(conn).id == next_id

    instagram = FakeInstagram(media_id="media-next")
    assert queue_worker.publish_next(conn, instagram) == "media-next"
    assert instagram.calls[0][1].startswith("다음 주제")


def test_media_publish_failure_is_not_retried_to_avoid_duplicate_posts(tmp_path):
    conn = make_conn(tmp_path)
    item_id = enqueue_item(conn, "게시됐을 수도 있는 주제")
    telegram = FakeTelegram()

    run_failing(conn, publish_forbidden_error(), telegram)

    assert queue_row(conn, item_id)["status"] == c.QUEUE_FAILED
    assert repo.next_in_queue(conn) is None
    assert "피드를 확인" in telegram.messages[0]


def test_account_level_failure_does_not_count_against_item(tmp_path):
    conn = make_conn(tmp_path)
    item_id = enqueue_item(conn, "주제")
    telegram = FakeTelegram()

    for _ in range(queue_worker.MAX_PUBLISH_ATTEMPTS + 1):
        run_failing(conn, expired_token_error(), telegram)

    row = queue_row(conn, item_id)
    assert row["status"] == c.QUEUE_QUEUED
    assert row["attempts"] == 0
    assert "그대로 둡니다" in telegram.messages[-1]


def test_failure_message_redacts_access_token(tmp_path):
    conn = make_conn(tmp_path)
    item_id = enqueue_item(conn, "주제")
    telegram = FakeTelegram()
    error = RuntimeError(
        "HTTPSConnectionPool(host='graph.instagram.com'): Max retries exceeded with url: "
        "/v21.0/123?fields=status_code&access_token=SECRET123 (Caused by timeout)"
    )

    run_failing(conn, error, telegram)

    assert "SECRET123" not in queue_row(conn, item_id)["last_error"]
    assert "access_token=***" in queue_row(conn, item_id)["last_error"]
    assert "SECRET123" not in telegram.messages[0]


def test_classify_publish_failure():
    classify = queue_worker.classify_publish_failure
    assert classify(caption_too_long_error()) == queue_worker.FAILURE_ITEM
    assert classify(publish_forbidden_error()) == queue_worker.FAILURE_UNCERTAIN
    assert classify(expired_token_error()) == queue_worker.FAILURE_ACCOUNT
    rate_limited = InstagramAPIError("limit", status=403, code=4, path="IGID/media")
    assert classify(rate_limited) == queue_worker.FAILURE_ACCOUNT
    assert classify(RuntimeError("timeout")) == queue_worker.FAILURE_RETRY
