"""슬라이드 텍스트 -> AI 배경 생성 -> 템플릿 합성까지 이어붙이는 파이프라인."""
from __future__ import annotations

from PIL import Image

from .. import constants as c
from ..config import get_config
from . import template
from .ai_background import ImageBackend

CATEGORY_LABELS = {
    c.CATEGORY_NEWS: "AI NEWS",
    c.CATEGORY_KNOWLEDGE: "AI KNOWLEDGE",
}


def build_background_prompt(topic: str, slide_text: str, is_thumbnail: bool) -> str:
    role = "cover" if is_thumbnail else "body"
    return (
        f"Background illustration for an Instagram carousel {role} slide. Minimal flat design, "
        f"subtle gradient, absolutely no text or lettering. Topic: {topic}. Scene related to: {slide_text}"
    )


def compose_slides(
    topic: str,
    category: str,
    slides: list[str],
    backend: ImageBackend,
    quality: str,
    style: template.BrandStyle | None = None,
) -> list[Image.Image]:
    style = style or template.load_brand_style(get_config())
    label = CATEGORY_LABELS.get(category, category.upper())

    images: list[Image.Image] = []
    for i, text in enumerate(slides):
        is_thumbnail = i == 0
        prompt = build_background_prompt(topic, text, is_thumbnail)
        background = backend.generate_background(prompt, quality)
        if is_thumbnail:
            image = template.render_thumbnail(background, topic, label, style)
        else:
            image = template.render_content_slide(background, text, i, len(slides), style)
        images.append(image)
    return images
