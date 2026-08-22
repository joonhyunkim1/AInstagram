"""고정 템플릿 렌더링.

AI 이미지 생성 모델이 텍스트까지 그리게 하면 매번 스타일이 흔들리고 글자도 깨지기 쉬워서,
배경만 AI로 만들고 제목/본문/브랜드 요소는 여기서 Pillow로 직접 합성한다.
그래야 피드 전체의 톤이 통일된다.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from ..config import AppConfig, ROOT_DIR


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


@dataclass
class BrandStyle:
    primary_color: tuple[int, int, int]
    overlay_opacity: int
    canvas_size: tuple[int, int]
    font_regular_path: str
    font_bold_path: str


def load_brand_style(cfg: AppConfig) -> BrandStyle:
    return BrandStyle(
        primary_color=_hex_to_rgb(cfg.image.brand.primary_color),
        overlay_opacity=cfg.image.brand.overlay_opacity,
        canvas_size=cfg.image.brand.canvas_size,
        font_regular_path=str(ROOT_DIR / cfg.image.font.regular),
        font_bold_path=str(ROOT_DIR / cfg.image.font.bold),
    )


def _load_font(path: str, size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _with_dark_overlay(base: Image.Image, opacity: int) -> Image.Image:
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = base.size
    draw.rectangle([0, int(h * 0.42), w, h], fill=(0, 0, 0, opacity))
    return Image.alpha_composite(base.convert("RGBA"), overlay)


def _draw_accent_bar(draw: ImageDraw.ImageDraw, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    w, h = size
    bar_height = max(int(h * 0.012), 4)
    draw.rectangle([0, h - bar_height, w, h], fill=color)


def render_thumbnail(
    background: Image.Image, topic: str, category_label: str, style: BrandStyle
) -> Image.Image:
    """1번째 슬라이드: 후킹용 썸네일. 피드 통일감을 위해 항상 같은 레이아웃을 쓴다."""
    canvas = _with_dark_overlay(background.resize(style.canvas_size), style.overlay_opacity)
    draw = ImageDraw.Draw(canvas)
    w, h = style.canvas_size

    tag_font = _load_font(style.font_bold_path, int(h * 0.032))
    title_font = _load_font(style.font_bold_path, int(h * 0.065))

    tag_text = category_label.upper()
    tag_padding = int(h * 0.018)
    tag_w = draw.textlength(tag_text, font=tag_font) + tag_padding * 2
    tag_h = int(h * 0.032) + tag_padding
    tag_x, tag_y = int(w * 0.06), int(h * 0.06)
    draw.rounded_rectangle(
        [tag_x, tag_y, tag_x + tag_w, tag_y + tag_h], radius=tag_h // 2, fill=style.primary_color
    )
    draw.text(
        (tag_x + tag_padding, tag_y + tag_padding // 2), tag_text, font=tag_font, fill=(255, 255, 255)
    )

    max_width = int(w * 0.85)
    lines = _wrap_text(draw, topic, title_font, max_width)
    line_height = int(h * 0.085)
    total_height = line_height * len(lines)
    start_y = int(h * 0.62) - total_height // 2
    for i, line in enumerate(lines):
        draw.text((int(w * 0.07), start_y + i * line_height), line, font=title_font, fill=(255, 255, 255))

    _draw_accent_bar(draw, style.canvas_size, style.primary_color)
    return canvas.convert("RGB")


def render_content_slide(
    background: Image.Image, text: str, index: int, total: int, style: BrandStyle
) -> Image.Image:
    """2번째 슬라이드부터: 본문. 페이지 번호 + 텍스트만 다르고 레이아웃은 썸네일과 통일."""
    canvas = _with_dark_overlay(background.resize(style.canvas_size), style.overlay_opacity)
    draw = ImageDraw.Draw(canvas)
    w, h = style.canvas_size

    page_font = _load_font(style.font_regular_path, int(h * 0.028))
    body_font = _load_font(style.font_bold_path, int(h * 0.048))

    draw.text(
        (int(w * 0.06), int(h * 0.05)),
        f"{index + 1} / {total}",
        font=page_font,
        fill=style.primary_color,
    )

    max_width = int(w * 0.85)
    lines = _wrap_text(draw, text, body_font, max_width)
    line_height = int(h * 0.062)
    total_height = line_height * len(lines)
    start_y = int(h * 0.62) - total_height // 2
    for i, line in enumerate(lines):
        draw.text((int(w * 0.07), start_y + i * line_height), line, font=body_font, fill=(255, 255, 255))

    _draw_accent_bar(draw, style.canvas_size, style.primary_color)
    return canvas.convert("RGB")
