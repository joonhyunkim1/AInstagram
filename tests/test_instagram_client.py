import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from ainstagram.publish.instagram_client import (
    ContainerProcessingError,
    ContainerProcessingTimeout,
    InstagramAPIError,
    InstagramClient,
)


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP error https://graph.instagram.com/v21.0/x?access_token=TOKEN")

    def json(self):
        return self._json_data


class FakeHttp:
    def __init__(self, post_responses=None, get_responses=None):
        self.post_responses = list(post_responses or [])
        self.get_responses = list(get_responses or [])
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs["data"]))
        return self._to_response(self.post_responses.pop(0))

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs["params"]))
        return self._to_response(self.get_responses.pop(0))

    @staticmethod
    def _to_response(item):
        return item if isinstance(item, FakeResponse) else FakeResponse(item)


def make_client(post_responses=None, get_responses=None):
    http = FakeHttp(post_responses=post_responses, get_responses=get_responses)
    client = InstagramClient(business_account_id="IGID", access_token="TOKEN", http=http)
    return client, http


def test_create_carousel_item_posts_expected_params():
    client, http = make_client(post_responses=[{"id": "item-1"}])
    item_id = client.create_carousel_item("https://cdn.example.com/1.jpg")

    assert item_id == "item-1"
    _, url, data = http.calls[0]
    assert url.endswith("/IGID/media")
    assert data["image_url"] == "https://cdn.example.com/1.jpg"
    assert data["is_carousel_item"] == "true"
    assert data["access_token"] == "TOKEN"


def test_create_carousel_container_joins_children_ids():
    client, http = make_client(post_responses=[{"id": "container-1"}])
    container_id = client.create_carousel_container(["item-1", "item-2"], "캡션")

    assert container_id == "container-1"
    _, _, data = http.calls[0]
    assert data["media_type"] == "CAROUSEL"
    assert data["children"] == "item-1,item-2"
    assert data["caption"] == "캡션"


def test_publish_posts_creation_id():
    client, http = make_client(post_responses=[{"id": "media-1"}])
    media_id = client.publish("container-1")

    assert media_id == "media-1"
    _, _, data = http.calls[0]
    assert data["creation_id"] == "container-1"


def test_get_container_status_returns_status_code():
    client, http = make_client(get_responses=[{"status_code": "IN_PROGRESS", "id": "c1"}])
    status = client.get_container_status("c1")

    assert status == "IN_PROGRESS"
    method, url, params = http.calls[0]
    assert method == "GET"
    assert url.endswith("/c1")
    assert params["fields"] == "status_code"


def test_wait_until_finished_returns_immediately_when_already_finished():
    client, http = make_client(get_responses=[{"status_code": "FINISHED"}])
    client.wait_until_finished("c1")  # 예외 없이 통과해야 함
    assert len(http.calls) == 1


def test_wait_until_finished_polls_until_finished(monkeypatch):
    client, http = make_client(
        get_responses=[{"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}]
    )
    monkeypatch.setattr("ainstagram.publish.instagram_client.time.sleep", lambda _: None)

    client.wait_until_finished("c1", interval=0)

    assert len(http.calls) == 2


def test_wait_until_finished_raises_on_error_status():
    client, http = make_client(get_responses=[{"status_code": "ERROR"}])

    with pytest.raises(ContainerProcessingError):
        client.wait_until_finished("c1")


def test_wait_until_finished_raises_timeout():
    client, http = make_client(get_responses=[{"status_code": "IN_PROGRESS"}])

    with pytest.raises(ContainerProcessingTimeout):
        client.wait_until_finished("c1", timeout=0, interval=0)


def test_publish_carousel_calls_in_expected_order(monkeypatch):
    monkeypatch.setattr("ainstagram.publish.instagram_client.time.sleep", lambda _: None)
    client, http = make_client(
        post_responses=[{"id": "item-1"}, {"id": "item-2"}, {"id": "container-1"}, {"id": "media-1"}],
        get_responses=[{"status_code": "FINISHED"}],
    )
    media_id = client.publish_carousel(
        ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"], "캡션"
    )

    assert media_id == "media-1"
    methods_and_urls = [(m, u) for m, u, _ in http.calls]
    assert methods_and_urls == [
        ("POST", "https://graph.instagram.com/v21.0/IGID/media"),
        ("POST", "https://graph.instagram.com/v21.0/IGID/media"),
        ("POST", "https://graph.instagram.com/v21.0/IGID/media"),
        ("GET", "https://graph.instagram.com/v21.0/container-1"),
        ("POST", "https://graph.instagram.com/v21.0/IGID/media_publish"),
    ]


def publish_error_response():
    return FakeResponse(
        {"error": {"message": "Application does not have permission", "type": "OAuthException", "code": 10}},
        status_code=403,
    )


def recent_media(caption, minutes_ago=0):
    posted_at = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "data": [
            {
                "id": "media-recovered",
                "caption": caption,
                "timestamp": posted_at.strftime("%Y-%m-%dT%H:%M:%S+0000"),
            }
        ]
    }


def test_api_error_message_includes_response_body_but_not_token():
    client, _ = make_client(post_responses=[publish_error_response()])

    with pytest.raises(InstagramAPIError) as exc_info:
        client.publish("container-1")

    message = str(exc_info.value)
    assert "403" in message
    assert "Application does not have permission" in message
    assert "code=10" in message
    assert "TOKEN" not in message


def test_publish_carousel_treats_error_as_success_when_post_actually_went_live(monkeypatch):
    monkeypatch.setattr("ainstagram.publish.instagram_client.time.sleep", lambda _: None)
    client, http = make_client(
        post_responses=[{"id": "item-1"}, {"id": "container-1"}, publish_error_response()],
        get_responses=[{"status_code": "FINISHED"}, recent_media("캡션  본문\n#tag")],
    )

    media_id = client.publish_carousel(["https://cdn.example.com/1.jpg"], "캡션 본문 #tag")

    assert media_id == "media-recovered"  # 공백/줄바꿈 차이는 무시하고 같은 캡션으로 판단
    assert http.calls[-1][1].endswith("/me/media")


def test_publish_carousel_reraises_when_no_matching_post_found(monkeypatch):
    monkeypatch.setattr("ainstagram.publish.instagram_client.time.sleep", lambda _: None)
    other = recent_media("다른 게시물 캡션")
    client, _ = make_client(
        post_responses=[{"id": "item-1"}, {"id": "container-1"}, publish_error_response()],
        get_responses=[{"status_code": "FINISHED"}, other, other, other],
    )

    with pytest.raises(InstagramAPIError, match="Application does not have permission"):
        client.publish_carousel(["https://cdn.example.com/1.jpg"], "캡션")


def test_publish_carousel_ignores_old_post_with_same_caption(monkeypatch):
    monkeypatch.setattr("ainstagram.publish.instagram_client.time.sleep", lambda _: None)
    old = {"data": [{"id": "old", "caption": "캡션", "timestamp": "2020-01-01T00:00:00+0000"}]}
    client, _ = make_client(
        post_responses=[{"id": "item-1"}, {"id": "container-1"}, publish_error_response()],
        get_responses=[{"status_code": "FINISHED"}, old, old, old],
    )

    with pytest.raises(InstagramAPIError):
        client.publish_carousel(["https://cdn.example.com/1.jpg"], "캡션")


def test_iter_media_follows_paging_cursor_newest_first():
    page1 = {"data": [{"id": "m3"}, {"id": "m2"}], "paging": {"cursors": {"after": "CUR1"}, "next": "https://x"}}
    page2 = {"data": [{"id": "m1"}], "paging": {"cursors": {"after": "CUR2"}}}
    client, http = make_client(get_responses=[page1, page2])

    ids = [m["id"] for m in client.iter_media(page_size=2)]

    assert ids == ["m3", "m2", "m1"]
    assert "after" not in http.calls[0][2]
    assert http.calls[1][2]["after"] == "CUR1"
    assert http.calls[0][1].endswith("/me/media")
