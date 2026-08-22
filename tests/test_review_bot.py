import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image

from ainstagram import constants as c
from ainstagram import repository as repo
from ainstagram.config import get_config
from ainstagram.db import get_connection
from ainstagram.review import bot


def make_conn(tmp_path):
    return get_connection(tmp_path / "test.db")


class FakeImageBackend:
    def generate_background(self, prompt, quality, size="1024x1024"):
        return Image.new("RGB", (64, 64), color=(100, 100, 100))


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)


class FakeTelegram:
    def __init__(self, update_batches=None):
        self.update_batches = list(update_batches or [])
        self.sent = []
        self.answered = []

    def send_photo_with_buttons(self, image_bytes, caption, buttons):
        self.sent.append((caption, buttons))
        return {"ok": True}

    def get_updates(self, offset=None):
        if not self.update_batches:
            return []
        return self.update_batches.pop(0)

    def answer_callback_query(self, callback_query_id, text):
        self.answered.append((callback_query_id, text))


def make_draft(conn, topic="주제1"):
    return repo.create_draft(
        conn,
        category=c.CATEGORY_NEWS,
        topic=topic,
        caption="캡션",
        slides=["표지 문구", "본문1", "본문2"],
    )


def callback_update(update_id, action, draft_id, callback_id="cb-1"):
    return {
        "update_id": update_id,
        "callback_query": {"id": callback_id, "data": f"{action}:{draft_id}"},
    }


def test_send_drafts_for_review_sends_one_preview_per_pending_draft(tmp_path):
    conn = make_conn(tmp_path)
    make_draft(conn, "주제1")
    make_draft(conn, "주제2")

    telegram = FakeTelegram()
    sent_count = bot.send_drafts_for_review(conn, telegram, FakeImageBackend())

    assert sent_count == 2
    assert len(telegram.sent) == 2
    caption, buttons = telegram.sent[0]
    assert "주제" in caption
    assert {b["callback_data"].split(":")[0] for b in buttons} == {
        bot.ACTION_APPROVE,
        bot.ACTION_APPROVE_TOP,
        bot.ACTION_DISCARD,
    }


def test_process_pending_reviews_discard(tmp_path):
    conn = make_conn(tmp_path)
    draft_id = make_draft(conn)
    telegram = FakeTelegram([[callback_update(1, bot.ACTION_DISCARD, draft_id)]])

    offset = bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    draft = repo.get_draft(conn, draft_id)
    assert draft.status == c.DRAFT_DISCARDED
    assert repo.next_in_queue(conn) is None
    assert offset == 2
    assert telegram.answered[0][1] == "폐기했습니다."


def test_process_pending_reviews_approve_enqueues_with_default_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.example.com")

    conn = make_conn(tmp_path)
    draft_id = make_draft(conn)
    telegram = FakeTelegram([[callback_update(1, bot.ACTION_APPROVE, draft_id)]])

    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    draft = repo.get_draft(conn, draft_id)
    assert draft.status == c.DRAFT_APPROVED

    queued = repo.next_in_queue(conn)
    assert queued.draft_id == draft_id
    assert queued.priority == 100  # 큐가 비어있을 때 기본값
    assert len(queued.image_urls) == 3  # 슬라이드 3장 전부 업로드


def test_process_pending_reviews_approve_top_uses_priority_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.example.com")

    conn = make_conn(tmp_path)
    draft_id = make_draft(conn)
    telegram = FakeTelegram([[callback_update(1, bot.ACTION_APPROVE_TOP, draft_id)]])

    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    queued = repo.next_in_queue(conn)
    assert queued.priority == 0


def test_process_pending_reviews_ignores_already_processed_draft(tmp_path):
    conn = make_conn(tmp_path)
    draft_id = make_draft(conn)
    repo.discard_draft(conn, draft_id)
    telegram = FakeTelegram([[callback_update(1, bot.ACTION_APPROVE, draft_id)]])

    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    assert telegram.answered[0][1] == "이미 처리된 초안입니다."
