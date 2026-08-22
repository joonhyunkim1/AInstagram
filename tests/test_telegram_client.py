import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram.review.telegram_client import TelegramClient


class FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


class FakeHttp:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse({"ok": True, "result": {"message_id": 1}})

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse({"ok": True, "result": [{"update_id": 5}]})


def make_client():
    http = FakeHttp()
    client = TelegramClient(bot_token="TOKEN", chat_id="123", http=http)
    return client, http


def test_send_photo_with_buttons_posts_expected_payload():
    client, http = make_client()
    client.send_photo_with_buttons(
        b"binary", "caption text", [{"text": "채택", "callback_data": "approve:1"}]
    )

    method, url, kwargs = http.calls[0]
    assert method == "POST"
    assert url.endswith("/sendPhoto")
    assert kwargs["data"]["chat_id"] == "123"
    assert kwargs["data"]["caption"] == "caption text"
    assert "approve:1" in kwargs["data"]["reply_markup"]
    assert kwargs["files"]["photo"][0] == "preview.jpg"


def test_get_updates_returns_result_list():
    client, http = make_client()
    updates = client.get_updates(offset=10)
    assert updates == [{"update_id": 5}]
    assert http.calls[0][2]["params"]["offset"] == 10


def test_answer_callback_query_posts_expected_payload():
    client, http = make_client()
    client.answer_callback_query("cb-1", "완료")
    method, url, kwargs = http.calls[0]
    assert url.endswith("/answerCallbackQuery")
    assert kwargs["data"] == {"callback_query_id": "cb-1", "text": "완료"}
