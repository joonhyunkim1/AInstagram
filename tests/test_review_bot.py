import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image

from ainstagram import constants as c
from ainstagram import repository as repo
from ainstagram.db import get_connection
from ainstagram.review import bot


def make_conn(tmp_path):
    return get_connection(tmp_path / "test.db")


def flat_callback_data(buttons):
    return {b["callback_data"] for row in buttons for b in row}


def flat_actions(buttons):
    return {b["callback_data"].split(":")[0] for row in buttons for b in row}


class FakeImageBackend:
    def __init__(self):
        self.call_count = 0

    def generate_background(self, prompt, quality, size="1024x1024"):
        self.call_count += 1
        return Image.new("RGB", (64, 64), color=(100, 100, 100))


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)


class FakeTelegram:
    def __init__(self, update_batches=None, downloaded_file_bytes=b"uploaded-bytes"):
        self.update_batches = list(update_batches or [])
        self.sent = []
        self.media_groups = []
        self.messages = []
        self.answered = []
        self.downloaded_file_bytes = downloaded_file_bytes
        self.downloaded_file_ids = []

    def send_photo_with_buttons(self, image_bytes, caption, buttons):
        self.sent.append((caption, buttons))
        return {"ok": True}

    def send_media_group(self, images):
        self.media_groups.append(images)
        return {"ok": True}

    def send_message(self, text, buttons=None):
        self.messages.append((text, buttons))
        return {"ok": True}

    def get_updates(self, offset=None):
        if not self.update_batches:
            return []
        return self.update_batches.pop(0)

    def answer_callback_query(self, callback_query_id, text):
        self.answered.append((callback_query_id, text))

    def download_file(self, file_id):
        self.downloaded_file_ids.append(file_id)
        return self.downloaded_file_bytes


def make_draft(conn, topic="주제1", slides=None):
    return repo.create_draft(
        conn,
        category=c.CATEGORY_NEWS,
        topic=topic,
        caption="캡션",
        slides=slides or ["표지 문구", "본문1", "본문2"],
    )


def callback_update(update_id, action, draft_id, callback_id="cb-1"):
    return {
        "update_id": update_id,
        "callback_query": {"id": callback_id, "data": f"{action}:{draft_id}"},
    }


def raw_callback_update(update_id, data, callback_id="cb-1"):
    return {"update_id": update_id, "callback_query": {"id": callback_id, "data": data}}


def message_update(update_id, text):
    return {"update_id": update_id, "message": {"text": text}}


def photo_message_update(update_id, file_id="file-1"):
    return {"update_id": update_id, "message": {"photo": [{"file_id": file_id}]}}


def make_ready_draft(tmp_path, monkeypatch, slides=None):
    """R2 env까지 세팅하고, 검수 전송(이미지 렌더링+저장)까지 마친 draft를 만든다."""
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.example.com")
    conn = make_conn(tmp_path)
    draft_id = make_draft(conn, slides=slides)
    telegram = FakeTelegram()
    bot.send_drafts_for_review(conn, telegram, FakeImageBackend(), FakeS3Client())
    return conn, draft_id


def test_send_drafts_for_review_sends_full_slide_album_per_pending_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.example.com")

    conn = make_conn(tmp_path)
    d1 = make_draft(conn, "주제1")
    make_draft(conn, "주제2")

    telegram = FakeTelegram()
    sent_count = bot.send_drafts_for_review(conn, telegram, FakeImageBackend(), FakeS3Client())

    assert sent_count == 2
    assert len(telegram.media_groups) == 2
    assert len(telegram.media_groups[0]) == 3  # 슬라이드 3장 전부 앨범으로 전송
    assert len(telegram.messages) == 2

    caption, buttons = telegram.messages[0]
    assert "주제" in caption
    assert flat_actions(buttons) == {
        bot.ACTION_APPROVE,
        bot.ACTION_APPROVE_TOP,
        bot.ACTION_EDIT_MENU,
        bot.ACTION_DISCARD,
    }

    draft = repo.get_draft(conn, d1)
    assert draft.image_urls is not None
    assert len(draft.image_urls) == 3


def test_send_drafts_for_review_skips_drafts_already_sent(tmp_path, monkeypatch):
    """검수 요청까지 보내고 아직 응답을 안 받은 예전 초안은, 다시 호출해도
    또 렌더링하거나 다시 전송하지 않아야 한다 (비용/중복 메시지 방지)."""
    conn, already_sent_id = make_ready_draft(tmp_path, monkeypatch)
    image_backend = FakeImageBackend()
    new_id = make_draft(conn, "새 주제")

    telegram = FakeTelegram()
    sent_count = bot.send_drafts_for_review(conn, telegram, image_backend, FakeS3Client())

    assert sent_count == 1
    assert image_backend.call_count == 3  # 새 초안(슬라이드 3장)만 새로 만듦
    assert len(telegram.messages) == 1
    assert "새 주제" in telegram.messages[0][0]


def test_approve_reuses_images_rendered_during_review(tmp_path, monkeypatch):
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.example.com")

    conn = make_conn(tmp_path)
    make_draft(conn, "주제1")

    image_backend = FakeImageBackend()
    telegram = FakeTelegram()
    bot.send_drafts_for_review(conn, telegram, image_backend, FakeS3Client())
    calls_after_review = image_backend.call_count
    assert calls_after_review > 0

    draft_id = repo.list_pending_drafts(conn)[0].id
    telegram2 = FakeTelegram([[callback_update(1, bot.ACTION_APPROVE, draft_id)]])
    bot.process_pending_reviews(conn, telegram2, image_backend, FakeS3Client())

    # 채택 시 검수 단계에서 만든 이미지를 재사용해야 하므로, 배경 생성 호출이 늘어나지 않아야 함
    assert image_backend.call_count == calls_after_review


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
    assert "폐기했습니다" in telegram.messages[0][0]
    assert "주제1" in telegram.messages[0][0]


def test_process_updates_handles_single_update_directly(tmp_path):
    """웹훅 경로(scripts/poll_reviews.py)가 하는 것처럼, get_updates/offset 없이
    업데이트 하나를 바로 process_updates에 넘겨도 동일하게 처리되어야 한다."""
    conn = make_conn(tmp_path)
    draft_id = make_draft(conn)
    telegram = FakeTelegram()

    bot.process_updates(
        conn, [callback_update(1, bot.ACTION_DISCARD, draft_id)], telegram, FakeImageBackend(), FakeS3Client()
    )

    draft = repo.get_draft(conn, draft_id)
    assert draft.status == c.DRAFT_DISCARDED
    assert telegram.answered[0][1] == "폐기했습니다."
    assert "폐기했습니다" in telegram.messages[0][0]


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


def test_send_queue_status_empty(tmp_path):
    conn = make_conn(tmp_path)
    telegram = FakeTelegram()

    count = bot.send_queue_status(conn, telegram)

    assert count == 0
    assert telegram.messages == [("현재 대기열이 비어있습니다.", None)]


def test_send_queue_status_lists_items_in_order(tmp_path):
    conn = make_conn(tmp_path)
    d1 = make_draft(conn, "주제A")
    d2 = make_draft(conn, "주제B")
    repo.enqueue(conn, d1, "c1", ["url1"], priority=50)
    q2 = repo.enqueue(conn, d2, "c2", ["url2"], priority=10)

    telegram = FakeTelegram()
    count = bot.send_queue_status(conn, telegram)

    assert count == 2
    first_text, first_buttons = telegram.messages[0]
    assert "주제B" in first_text  # 우선순위 낮은 게 먼저
    assert flat_actions(first_buttons) == {bot.ACTION_QUEUE_BUMP, bot.ACTION_QUEUE_REMOVE}
    assert first_buttons[0][0]["callback_data"] == f"{bot.ACTION_QUEUE_BUMP}:{q2}"


def test_process_pending_reviews_routes_queue_command(tmp_path):
    conn = make_conn(tmp_path)
    d1 = make_draft(conn, "주제A")
    repo.enqueue(conn, d1, "c1", ["url1"], priority=10)

    telegram = FakeTelegram([[message_update(1, "/queue")]])
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    assert len(telegram.messages) == 1
    assert "주제A" in telegram.messages[0][0]


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def generate_topics(self, category, context, count):
        self.calls += 1
        return [
            {"topic": f"생성된 주제{i}", "caption": f"캡션{i}", "slides": ["표지", "본문1"]}
            for i in range(count)
        ]

    def embed(self, text):
        return [0.0, 0.0]


def test_process_pending_reviews_generate_command_creates_and_sends_drafts(tmp_path, monkeypatch):
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.example.com")
    conn = make_conn(tmp_path)
    telegram = FakeTelegram([[message_update(1, "/generate")]])
    llm = FakeLLM()

    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client(), llm=llm)

    assert llm.calls >= 1
    pending = repo.list_pending_drafts(conn)
    assert len(pending) == 3
    assert len(telegram.media_groups) == 3
    assert any("생성" in text for text, _ in telegram.messages)
    assert any("생성 완료" in text for text, _ in telegram.messages)


def test_process_pending_reviews_queue_bump(tmp_path):
    conn = make_conn(tmp_path)
    d1 = make_draft(conn, "주제A")
    d2 = make_draft(conn, "주제B")
    repo.enqueue(conn, d1, "c1", ["url1"], priority=10)
    q2 = repo.enqueue(conn, d2, "c2", ["url2"], priority=20)

    telegram = FakeTelegram([[callback_update(1, bot.ACTION_QUEUE_BUMP, q2)]])
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    items = repo.list_queue(conn)
    assert items[0].id == q2
    assert telegram.answered[0][1] == "대기열 맨 앞으로 옮겼습니다."


def test_process_pending_reviews_queue_remove(tmp_path):
    conn = make_conn(tmp_path)
    d1 = make_draft(conn, "주제A")
    q1 = repo.enqueue(conn, d1, "c1", ["url1"], priority=10)

    telegram = FakeTelegram([[callback_update(1, bot.ACTION_QUEUE_REMOVE, q1)]])
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    assert repo.list_queue(conn) == []
    draft = repo.get_draft(conn, d1)
    assert draft.status == c.DRAFT_DISCARDED
    assert telegram.answered[0][1] == "대기열에서 제거했습니다."


# ---- 수정 메뉴 ----


def test_edit_menu_action_sends_submenu(tmp_path, monkeypatch):
    conn, draft_id = make_ready_draft(tmp_path, monkeypatch)

    telegram = FakeTelegram([[callback_update(1, bot.ACTION_EDIT_MENU, draft_id)]])
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    text, buttons = telegram.messages[-1]
    assert "수정" in text
    assert flat_actions(buttons) == {
        bot.ACTION_DELETE_IMAGE_MENU,
        bot.ACTION_ADD_IMAGE_AI,
        bot.ACTION_ADD_IMAGE_UPLOAD,
        bot.ACTION_EDIT_CAPTION,
        bot.ACTION_EDIT_CANCEL,
    }


def test_edit_menu_refuses_on_already_processed_draft(tmp_path):
    conn = make_conn(tmp_path)
    draft_id = make_draft(conn)
    repo.discard_draft(conn, draft_id)

    telegram = FakeTelegram([[callback_update(1, bot.ACTION_EDIT_MENU, draft_id)]])
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    assert telegram.answered[0][1] == "이미 처리된 초안이라 수정할 수 없습니다."
    assert telegram.messages == []


def test_delete_image_menu_lists_slide_numbers(tmp_path, monkeypatch):
    conn, draft_id = make_ready_draft(tmp_path, monkeypatch)

    telegram = FakeTelegram([[callback_update(1, bot.ACTION_DELETE_IMAGE_MENU, draft_id)]])
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    text, buttons = telegram.messages[-1]
    assert "번호" in text
    numbers = {b["text"] for row in buttons for b in row if b["callback_data"].startswith(bot.ACTION_DELETE_IMAGE + ":")}
    assert numbers == {"1", "2", "3"}


def test_delete_image_removes_slide_and_resends_preview(tmp_path, monkeypatch):
    conn, draft_id = make_ready_draft(tmp_path, monkeypatch)

    telegram = FakeTelegram([[raw_callback_update(1, f"{bot.ACTION_DELETE_IMAGE}:{draft_id}:1")]])
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    draft = repo.get_draft(conn, draft_id)
    assert draft.slides == ["표지 문구", "본문2"]
    assert len(draft.image_urls) == 2
    assert telegram.answered[0][1] == "2번 슬라이드를 삭제했습니다."
    assert len(telegram.media_groups[-1]) == 2  # 삭제 후 미리보기 재전송


def test_delete_image_refuses_when_only_one_left(tmp_path, monkeypatch):
    conn, draft_id = make_ready_draft(tmp_path, monkeypatch, slides=["표지 문구"])

    telegram = FakeTelegram([[raw_callback_update(1, f"{bot.ACTION_DELETE_IMAGE}:{draft_id}:0")]])
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    draft = repo.get_draft(conn, draft_id)
    assert len(draft.image_urls) == 1
    assert telegram.answered[0][1] == "이미지가 1장뿐이라 삭제할 수 없습니다."


def test_add_image_ai_flow_appends_new_slide(tmp_path, monkeypatch):
    conn, draft_id = make_ready_draft(tmp_path, monkeypatch)

    telegram = FakeTelegram(
        [
            [callback_update(1, bot.ACTION_ADD_IMAGE_AI, draft_id)],
            [message_update(2, "새로 추가하는 슬라이드 문구입니다.")],
        ]
    )
    image_backend = FakeImageBackend()
    bot.process_pending_reviews(conn, telegram, image_backend, FakeS3Client())
    bot.process_pending_reviews(conn, telegram, image_backend, FakeS3Client())

    draft = repo.get_draft(conn, draft_id)
    assert draft.slides[-1] == "새로 추가하는 슬라이드 문구입니다."
    assert len(draft.image_urls) == 4
    assert len(telegram.media_groups[-1]) == 4


def test_add_image_upload_flow_appends_uploaded_photo(tmp_path, monkeypatch):
    conn, draft_id = make_ready_draft(tmp_path, monkeypatch)

    telegram = FakeTelegram(
        [
            [callback_update(1, bot.ACTION_ADD_IMAGE_UPLOAD, draft_id)],
            [photo_message_update(2, file_id="my-file-id")],
        ],
        downloaded_file_bytes=_tiny_jpeg_bytes(),
    )
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    draft = repo.get_draft(conn, draft_id)
    assert len(draft.image_urls) == 4
    assert draft.slides[-1] == ""
    assert telegram.downloaded_file_ids == ["my-file-id"]


def test_edit_caption_flow_replaces_caption(tmp_path, monkeypatch):
    conn, draft_id = make_ready_draft(tmp_path, monkeypatch)

    telegram = FakeTelegram(
        [
            [callback_update(1, bot.ACTION_EDIT_CAPTION, draft_id)],
            [message_update(2, "새로운 캡션입니다.")],
        ]
    )
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    draft = repo.get_draft(conn, draft_id)
    assert draft.caption == "새로운 캡션입니다."


def test_edit_cancel_clears_pending_action(tmp_path, monkeypatch):
    conn, draft_id = make_ready_draft(tmp_path, monkeypatch)

    telegram = FakeTelegram(
        [
            [callback_update(1, bot.ACTION_EDIT_CAPTION, draft_id)],
            [callback_update(2, bot.ACTION_EDIT_CANCEL, draft_id)],
            [message_update(3, "이건 캡션으로 반영되면 안 됨")],
        ]
    )
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())
    bot.process_pending_reviews(conn, telegram, FakeImageBackend(), FakeS3Client())

    draft = repo.get_draft(conn, draft_id)
    assert draft.caption == "캡션"  # 원래 캡션 그대로


def _tiny_jpeg_bytes() -> bytes:
    import io as _io

    buf = _io.BytesIO()
    Image.new("RGB", (8, 8), color=(50, 60, 70)).save(buf, format="JPEG")
    return buf.getvalue()
