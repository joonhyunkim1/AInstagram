"""슬라이드 텍스트 -> AI 배경 생성 -> 템플릿 합성까지 이어붙이는 파이프라인."""
from __future__ import annotations

from PIL import Image

from .. import constants as c
from ..config import get_config
from . import template
from .ai_background import ImageBackend, ImageGenerationBlocked

CATEGORY_LABELS = {
    c.CATEGORY_NEWS: "AI NEWS",
    c.CATEGORY_KNOWLEDGE: "AI KNOWLEDGE",
}


def build_background_prompt(topic: str, slide_text: str, is_thumbnail: bool) -> str:
    role = "cover" if is_thumbnail else "body"
    return (
        f"Photorealistic editorial photograph for an Instagram carousel {role} slide, shot to grab "
        f"attention - striking composition, dramatic lighting, or a bold close-up angle, like the "
        f"photo a major news outlet would pick to make people stop scrolling. Real-world scene, "
        f"realistic textures and depth of field - not an illustration, not a cartoon, not flat "
        f"design, not a 3D render. Do NOT depict people, faces, or portraits.\n"
        f"Read this slide's specific point below and photograph the single most concrete, distinctive, "
        f"visually striking detail in it (a specific object, symbol, screen content, place, or "
        f"consequence), so that someone could guess what this slide is about just by looking at the "
        f"image. Do NOT default to a generic laptop-with-a-chart shot or a generic office/tech stock "
        f"photo. Absolutely no text or lettering anywhere in the image.\n"
        f"Overall topic: {topic}\n"
        f"This slide's specific point - make the image about THIS, not the general topic: {slide_text}"
    )


def build_fallback_background_prompt(topic: str, is_thumbnail: bool) -> str:
    """1차 프롬프트가 안전 필터에 막혔을 때 쓰는, 훨씬 중립적인 대체 프롬프트."""
    role = "cover" if is_thumbnail else "body"
    return (
        f"Photorealistic editorial photograph for an Instagram carousel {role} slide about the "
        f"general topic of {topic}. Neutral, safe-for-work, real-world tech/business scene - a "
        f"relevant object, device, or environment, shot with realistic lighting and depth of field. "
        f"Not an illustration, not a cartoon, not flat design. Do NOT depict people, faces, or "
        f"portraits. Absolutely no text or lettering anywhere in the image."
    )


def compose_single_slide(
    topic: str,
    slide_text: str,
    index: int,
    total: int,
    is_thumbnail: bool,
    backend: ImageBackend,
    quality: str,
    style: template.BrandStyle,
    category_label: str = "",
) -> Image.Image:
    """슬라이드 한 장만 렌더링한다. 대기열 검수 중 슬라이드를 한 장 추가할 때도 재사용."""
    prompt = build_background_prompt(topic, slide_text, is_thumbnail)
    try:
        background = backend.generate_background(prompt, quality)
    except ImageGenerationBlocked:
        # 특정 슬라이드 내용이 안전 필터에 걸리면, 훨씬 중립적인 대체 프롬프트로 재시도한다
        fallback_prompt = build_fallback_background_prompt(topic, is_thumbnail)
        background = backend.generate_background(fallback_prompt, quality)
    if is_thumbnail:
        return template.render_thumbnail(background, topic, category_label, style)
    return template.render_content_slide(background, slide_text, index, total, style)


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

    return [
        compose_single_slide(
            topic, text, i, len(slides), i == 0, backend, quality, style, label
        )
        for i, text in enumerate(slides)
    ]
