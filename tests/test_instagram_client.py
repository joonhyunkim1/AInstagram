import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram.publish.instagram_client import InstagramClient


class FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs["data"]))
        return FakeResponse(self.responses.pop(0))


def make_client(responses):
    http = FakeHttp(responses)
    client = InstagramClient(business_account_id="IGID", access_token="TOKEN", http=http)
    return client, http


def test_create_carousel_item_posts_expected_params():
    client, http = make_client([{"id": "item-1"}])
    item_id = client.create_carousel_item("https://cdn.example.com/1.jpg")

    assert item_id == "item-1"
    url, data = http.calls[0]
    assert url.endswith("/IGID/media")
    assert data["image_url"] == "https://cdn.example.com/1.jpg"
    assert data["is_carousel_item"] == "true"
    assert data["access_token"] == "TOKEN"


def test_create_carousel_container_joins_children_ids():
    client, http = make_client([{"id": "container-1"}])
    container_id = client.create_carousel_container(["item-1", "item-2"], "캡션")

    assert container_id == "container-1"
    _, data = http.calls[0]
    assert data["media_type"] == "CAROUSEL"
    assert data["children"] == "item-1,item-2"
    assert data["caption"] == "캡션"


def test_publish_posts_creation_id():
    client, http = make_client([{"id": "media-1"}])
    media_id = client.publish("container-1")

    assert media_id == "media-1"
    _, data = http.calls[0]
    assert data["creation_id"] == "container-1"


def test_publish_carousel_calls_in_expected_order():
    client, http = make_client(
        [{"id": "item-1"}, {"id": "item-2"}, {"id": "container-1"}, {"id": "media-1"}]
    )
    media_id = client.publish_carousel(
        ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"], "캡션"
    )

    assert media_id == "media-1"
    urls = [url for url, _ in http.calls]
    assert urls == [
        "https://graph.facebook.com/v21.0/IGID/media",
        "https://graph.facebook.com/v21.0/IGID/media",
        "https://graph.facebook.com/v21.0/IGID/media",
        "https://graph.facebook.com/v21.0/IGID/media_publish",
    ]
