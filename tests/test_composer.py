import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image

from ainstagram import constants as c
from ainstagram.config import get_config
from ainstagram.images import composer
from ainstagram.images.ai_background import ImageGenerationBlocked


class FakeBackend:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def generate_background(self, prompt, quality, size="1024x1024"):
        self.calls.append((prompt, quality))
        return Image.new("RGB", (256, 256), color=(120, 120, 120))


class FakeBackendBlocksFirstCall:
    """첫 호출(원본 프롬프트)만 안전 필터에 막힌 것처럼 동작하는 가짜 백엔드."""

    def __init__(self):
        self.prompts: list[str] = []

    def generate_background(self, prompt, quality, size="1024x1024"):
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            raise ImageGenerationBlocked("blocked: sexual")
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
    assert "text" in prompt or "lettering" in prompt


def test_compose_slides_retries_with_fallback_prompt_when_blocked():
    backend = FakeBackendBlocksFirstCall()
    slides = ["민감한 후킹 문구"]

    images = composer.compose_slides("민감한 주제", c.CATEGORY_NEWS, slides, backend, quality="low")

    assert len(images) == 1  # 막혀도 대체 프롬프트로 재시도해서 결국 만들어짐
    assert len(backend.prompts) == 2  # 원본 프롬프트 1회 + 대체 프롬프트 1회
    # 대체 프롬프트도 민감한 표현은 빼되, 슬라이드의 핵심 시각 소재는 유지하기 위해
    # 슬라이드 텍스트 자체는 그대로 포함한다
    assert "민감한 후킹 문구" in backend.prompts[1]
