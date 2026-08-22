import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image

from ainstagram.config import get_config
from ainstagram.images import template


def test_render_thumbnail_matches_canvas_size():
    style = template.load_brand_style(get_config())
    bg = Image.new("RGB", (100, 100), color=(50, 50, 50))
    img = template.render_thumbnail(bg, "GPT-5 출시로 달라지는 것들 총정리", "AI NEWS", style)
    assert img.size == style.canvas_size
    assert img.mode == "RGB"


def test_render_content_slide_matches_canvas_size():
    style = template.load_brand_style(get_config())
    bg = Image.new("RGB", (100, 100), color=(10, 10, 10))
    img = template.render_content_slide(bg, "본문 슬라이드 테스트 문장입니다.", 1, 6, style)
    assert img.size == style.canvas_size


def test_render_thumbnail_wraps_long_topic_without_error():
    style = template.load_brand_style(get_config())
    bg = Image.new("RGB", (100, 100), color=(80, 80, 80))
    long_topic = "이것은 아주 길게 작성된 인스타그램 카드뉴스 썸네일용 주제 문구 테스트입니다 " * 3
    img = template.render_thumbnail(bg, long_topic, "AI 지식", style)
    assert img.size == style.canvas_size
