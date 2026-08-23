"""고정 템플릿 렌더링.

AI 이미지 생성 모델이 텍스트까지 그리게 하면 매번 스타일이 흔들리고 글자도 깨지기 쉬워서,
배경만 AI로 만들고 제목/본문/브랜드 요소는 여기서 Pillow로 직접 합성한다.
그래야 피드 전체의 톤이 통일된다.

레이아웃은 텍스트를 하단에 바짝 붙이고, 어두운 오버레이도 텍스트 블록 바로 위에서부터
시작하도록 계산한다 (고정 비율로 넓게 깔면 텍스트와 이미지 사이에 불필요한 여백이 생긴다).
"""
from __future__ import annotations

import math
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


_UNSUPPORTED_CHAR_REPLACEMENTS = {
    "‑": "-",  # non-breaking hyphen - IBM Plex Sans KR에 글리프가 없어 네모 박스로 깨짐
    "­": "",  # soft hyphen - 마찬가지로 글리프가 없고, 원래도 화면에 안 보여야 하는 문자
    " ": " ",  # non-breaking space - 마찬가지로 글리프가 없음
}


def _sanitize_text(text: str) -> str:
    """LLM이 생성한 텍스트에 폰트가 지원하지 않는 특수 문자가 섞여 있으면
    렌더링 시 네모 박스(tofu box)로 깨지므로, 비슷한 일반 문자로 치환한다."""
    for char, replacement in _UNSUPPORTED_CHAR_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    return text


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """줄 길이를 최대한 균등하게 나눈다.

    첫 줄을 max_width까지 꽉 채우고 남는 단어를 마지막 줄에 몰아넣으면
    '긴 줄 + 짧은 한 단어' 같은 어색한 줄바꿈이 생기기 쉬워서,
    전체 폭으로 필요한 줄 수를 먼저 정하고 그 줄 수에 맞게 폭을 나눠 채운다.
    """
    words = text.split(" ")
    if len(words) <= 1:
        return [text]

    space_width = draw.textlength(" ", font=font)
    widths = [draw.textlength(w, font=font) for w in words]
    total_width = sum(widths) + space_width * (len(words) - 1)

    if total_width <= max_width:
        return [text]

    num_lines = max(2, math.ceil(total_width / max_width))
    target_width = total_width / num_lines

    lines: list[str] = []
    current_words: list[str] = []
    current_width = 0.0
    for word, width in zip(words, widths):
        added_width = width if not current_words else width + space_width
        would_exceed_target = current_width + added_width > target_width
        would_exceed_frame = current_width + added_width > max_width
        can_still_break = len(lines) < num_lines - 1
        if current_words and (would_exceed_frame or (would_exceed_target and can_still_break)):
            lines.append(" ".join(current_words))
            current_words = [word]
            current_width = width
        else:
            current_words.append(word)
            current_width += added_width
    if current_words:
        lines.append(" ".join(current_words))
    return lines


def _bottom_text_layout(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    canvas_size: tuple[int, int],
    side_margin_ratio: float,
    bottom_margin_ratio: float,
) -> tuple[list[str], int, int]:
    """텍스트를 하단 기준으로 배치할 때 필요한 줄/줄높이/시작 y좌표를 계산한다."""
    w, h = canvas_size
    max_width = int(w * (1 - side_margin_ratio * 2))
    lines = _wrap_text(draw, text, font, max_width)
    line_height = int(font.size * 1.15)
    total_height = line_height * len(lines)
    bottom_y = int(h * (1 - bottom_margin_ratio))
    start_y = bottom_y - total_height
    return lines, line_height, start_y


def _fit_bottom_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    base_size: int,
    min_size: int,
    canvas_size: tuple[int, int],
    side_margin_ratio: float,
    bottom_margin_ratio: float,
    max_top_ratio: float,
) -> tuple[ImageFont.ImageFont, list[str], int, int]:
    """텍스트 분량이 많아도 화면을 벗어나지 않도록, 안 맞으면 폰트 크기를 줄여가며 맞춘다."""
    _, h = canvas_size
    size = base_size
    font = _load_font(font_path, size)
    lines, line_height, start_y = _bottom_text_layout(
        draw, text, font, canvas_size, side_margin_ratio, bottom_margin_ratio
    )
    while start_y < int(h * max_top_ratio) and size > min_size:
        size = max(min_size, size - max(2, int(base_size * 0.06)))
        font = _load_font(font_path, size)
        lines, line_height, start_y = _bottom_text_layout(
            draw, text, font, canvas_size, side_margin_ratio, bottom_margin_ratio
        )
    return font, lines, line_height, start_y


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    line_height: int,
    start_y: int,
    x: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    for i, line in enumerate(lines):
        draw.text((x, start_y + i * line_height), line, font=font, fill=fill)


def _with_dark_overlay(base: Image.Image, opacity: int, top_y: int) -> Image.Image:
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = base.size
    draw.rectangle([0, max(0, top_y), w, h], fill=(0, 0, 0, opacity))
    return Image.alpha_composite(base.convert("RGBA"), overlay)


def _draw_accent_bar(draw: ImageDraw.ImageDraw, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    w, h = size
    bar_height = max(int(h * 0.012), 4)
    draw.rectangle([0, h - bar_height, w, h], fill=color)


SIDE_MARGIN_RATIO = 0.06
OVERLAY_TOP_PADDING_RATIO = 0.045


def render_thumbnail(background: Image.Image, topic: str, style: BrandStyle) -> Image.Image:
    """1번째 슬라이드: 후킹용 썸네일. 피드 통일감을 위해 항상 같은 레이아웃을 쓴다."""
    topic = _sanitize_text(topic)
    w, h = style.canvas_size
    raw = background.resize(style.canvas_size).convert("RGBA")
    probe_draw = ImageDraw.Draw(raw)

    title_font, lines, line_height, start_y = _fit_bottom_text(
        probe_draw,
        topic,
        style.font_bold_path,
        base_size=int(h * 0.068),
        min_size=int(h * 0.036),
        canvas_size=style.canvas_size,
        side_margin_ratio=SIDE_MARGIN_RATIO,
        bottom_margin_ratio=0.09,
        max_top_ratio=0.16,
    )

    overlay_top = start_y - int(h * OVERLAY_TOP_PADDING_RATIO)
    canvas = _with_dark_overlay(raw, style.overlay_opacity, overlay_top)
    draw = ImageDraw.Draw(canvas)

    _draw_lines(draw, lines, line_height, start_y, int(w * SIDE_MARGIN_RATIO), title_font, (255, 255, 255))
    _draw_accent_bar(draw, style.canvas_size, style.primary_color)
    return canvas.convert("RGB")


def render_content_slide(
    background: Image.Image, text: str, index: int, total: int, style: BrandStyle
) -> Image.Image:
    """2번째 슬라이드부터: 본문. 페이지 번호 + 텍스트만 다르고 레이아웃은 썸네일과 통일."""
    text = _sanitize_text(text)
    w, h = style.canvas_size
    raw = background.resize(style.canvas_size).convert("RGBA")
    probe_draw = ImageDraw.Draw(raw)

    body_font, lines, line_height, start_y = _fit_bottom_text(
        probe_draw,
        text,
        style.font_bold_path,
        base_size=int(h * 0.046),
        min_size=int(h * 0.028),
        canvas_size=style.canvas_size,
        side_margin_ratio=SIDE_MARGIN_RATIO,
        bottom_margin_ratio=0.09,
        max_top_ratio=0.12,
    )

    overlay_top = start_y - int(h * OVERLAY_TOP_PADDING_RATIO)
    canvas = _with_dark_overlay(raw, style.overlay_opacity, overlay_top)
    draw = ImageDraw.Draw(canvas)

    page_font = _load_font(style.font_regular_path, int(h * 0.028))
    draw.text(
        (int(w * 0.06), int(h * 0.05)),
        f"{index + 1} / {total}",
        font=page_font,
        fill=style.primary_color,
    )

    _draw_lines(draw, lines, line_height, start_y, int(w * SIDE_MARGIN_RATIO), body_font, (255, 255, 255))
    _draw_accent_bar(draw, style.canvas_size, style.primary_color)
    return canvas.convert("RGB")
