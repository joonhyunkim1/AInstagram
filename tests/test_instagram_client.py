import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from ainstagram.publish.instagram_client import (
    ContainerProcessingError,
    ContainerProcessingTimeout,
    InstagramClient,
)


class FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


class FakeHttp:
    def __init__(self, post_responses=None, get_responses=None):
        self.post_responses = list(post_responses or [])
        self.get_responses = list(get_responses or [])
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs["data"]))
        return FakeResponse(self.post_responses.pop(0))

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs["params"]))
        return FakeResponse(self.get_responses.pop(0))


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
