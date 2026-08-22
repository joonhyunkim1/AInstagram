import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image

from ainstagram import constants as c
from ainstagram.config import get_config
from ainstagram.images import composer


class FakeBackend:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def generate_background(self, prompt, quality, size="1024x1024"):
        self.calls.append((prompt, quality))
        return Image.new("RGB", (256, 256), color=(120, 120, 120))


def test_compose_slides_generates_one_background_per_slide():
    backend = FakeBackend()
    slides = ["표지 후킹 문구", "본문 내용 1", "본문 내용 2"]

    images = composer.compose_slides("트랜스포머 구조 이해하기", c.CATEGORY_KNOWLEDGE, slides, backend, quality="low")

    assert len(images) == 3
    assert len(backend.calls) == 3
    canvas_size = tuple(get_config().image.brand.canvas_size)
    assert all(img.size == canvas_size for img in images)


def test_build_background_prompt_excludes_text_instruction():
    prompt = composer.build_background_prompt("주제", "슬라이드 내용", is_thumbnail=True)
    assert "텍스트" in prompt or "글자" in prompt
