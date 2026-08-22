import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image

from ainstagram.images import storage


class FakeS3Client:
    def __init__(self):
        self.calls: list[dict] = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)


def test_upload_image_returns_public_url(monkeypatch):
    monkeypatch.setenv("R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.example.com/")

    client = FakeS3Client()
    image = Image.new("RGB", (10, 10), color=(1, 2, 3))

    url = storage.upload_image(client, image, "slides/1.jpg")

    assert url == "https://cdn.example.com/slides/1.jpg"
    assert client.calls[0]["Bucket"] == "test-bucket"
    assert client.calls[0]["Key"] == "slides/1.jpg"
    assert client.calls[0]["ContentType"] == "image/jpeg"
