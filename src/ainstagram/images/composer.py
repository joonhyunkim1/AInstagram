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


def build_background_prompt(
    topic: str,
    slide_text: str,
    is_thumbnail: bool,
) -> str:
    role = "cover" if is_thumbnail else "body"

    if is_thumbnail:
        role_instructions = """
This is the COVER slide.

The image must work as a strong visual hook before the viewer reads the headline.
Use one dominant, immediately recognizable visual subject.
Favor bold composition, strong visual hierarchy, dramatic but realistic lighting,
and a clear focal point.

The image should communicate the central subject or event at a glance rather than
trying to visualize every detail of the story.
Keep the composition relatively simple so the overlaid cover text remains readable.
"""
    else:
        role_instructions = """
This is a BODY slide.

The image should visually support the specific information being explained on this
slide rather than simply repeating the overall topic.

Choose a concrete visual subject, situation, consequence, process, or visual metaphor
that helps the viewer understand the slide.
Use a composition that feels different from a typical cover image and leaves enough
visual breathing room for the overlaid slide text.
"""

    return f"""
Create a photorealistic editorial photograph for an Instagram carousel about AI.

VISUAL STYLE
- Photorealistic editorial photography.
- Real-world materials, environments, lighting, textures, and depth of field.
- Sophisticated and modern, but believable rather than futuristic fantasy.
- Visually striking enough to stop scrolling.
- Think of high-quality technology, business, science, or magazine editorial photography.
- Use cinematic composition and intentional framing when appropriate.
- Do NOT make the image look like an advertisement, generic stock photography,
  a movie poster, or a promotional render.

{role_instructions}

VISUAL INTERPRETATION
Read the topic and the specific slide text together, but give priority to the
specific point being made on this slide.

First identify the strongest visual idea that could communicate the slide.
Prefer, in roughly this order:

1. A specific real-world object directly related to the story.
2. A recognizable environment or situation related to the story.
3. A meaningful action, process, or consequence.
4. A technically relevant detail or close-up.
5. A subtle visual metaphor when the concept is abstract and cannot be represented
   naturally with a concrete object.

Do not simply illustrate the words literally.
Find the most visually informative and distinctive representation of the idea.

For concrete news:
Show the actual type of object, device, environment, infrastructure, product,
or situation involved in the story whenever possible.

For technical or educational concepts:
Use a realistic representation of the technology or process.
When the concept is inherently abstract, use a restrained and intelligent visual
metaphor rather than a cliché futuristic image.

For business or company news:
Prefer relevant physical products, hardware, offices, infrastructure, manufacturing,
research environments, or real-world consequences over generic company imagery.

For research or scientific topics:
Prefer laboratories, instruments, chips, sensors, experiments, data-processing hardware,
microscopic or technical details, or realistic research environments when appropriate.

PEOPLE
People may appear when they naturally strengthen the visual story.
However:
- Do NOT create identifiable real people unless explicitly required by the slide.
- Avoid portraits or celebrity-style depictions.
- Prefer natural human activity, hands, silhouettes, side/back views, researchers,
  engineers, developers, workers, or crowds when people are useful.
- Do not make a human the subject merely because the topic involves AI.

AVOID AI VISUAL CLICHÉS
Do NOT use generic or overused AI imagery unless the slide specifically requires it:
- glowing artificial brains
- humanoid robots
- robot hands touching holograms
- floating AI holograms
- generic neural-network graphics
- blue glowing circuits
- futuristic digital brains
- cyberpunk cities
- generic server rooms
- generic laptops with charts
- generic people staring at holographic screens
- abstract blue technology backgrounds
- generic "AI" symbols
- excessive neon lighting
- sci-fi interfaces
- random futuristic technology

Do NOT add visual elements merely because they make the image look "AI".
The visual should come from the actual subject of the story.

VISUAL VARIETY
Avoid making every slide look like the same type of technology stock photo.
Across a carousel, naturally vary:
- camera angle
- framing
- scale
- environment
- subject
- lighting
- perspective
- visual metaphor

However, maintain a coherent editorial aesthetic across the carousel.

TEXT AND GRAPHICS
- Absolutely NO readable text, words, letters, captions, logos, labels,
  UI text, watermarks, or typography anywhere in the image.
- Do NOT generate fake headlines, fake article screenshots, fake charts with labels,
  fake interface text, or readable code.
- Avoid prominent logos unless they can appear naturally without readable text.
- The final Instagram template will add all text separately.

COMPOSITION
Leave sufficient negative space for the Instagram template to overlay text.
Do not place the main visual subject directly over the expected text area when possible.
For cover slides, prioritize a strong central focal point and clean visual hierarchy.
For body slides, prioritize informative composition and visual clarity.

SAFETY AND REALISM
Keep the scene safe for work and suitable for a mainstream technology news account.
Do not depict graphic violence, gore, sexual content, or disturbing imagery.
Do not fabricate impossible physical situations when a realistic interpretation is possible.

TOPIC
{topic}

THIS SLIDE
{slide_text}

Generate an image specifically for THIS slide.
Do not create a generic image representing only the overall topic.
"""


def build_fallback_background_prompt(
    topic: str,
    slide_text: str,
    is_thumbnail: bool,
) -> str:
    """
    Neutral fallback prompt used when the primary image-generation prompt
    is blocked by a safety filter.

    It intentionally removes potentially sensitive or ambiguous details while
    preserving the slide's core visual subject as much as possible.
    """
    role = "cover" if is_thumbnail else "body"

    if is_thumbnail:
        composition = """
Create a strong editorial cover image with one clear dominant subject,
simple composition, realistic lighting, and enough negative space for
large overlaid text.
"""
    else:
        composition = """
Create a clean editorial body-slide image that visually supports the
specific subject of the slide with a realistic object, environment,
process, or situation.
"""

    return f"""
Create a photorealistic editorial photograph for an Instagram carousel {role} slide.

{composition}

Use a realistic technology, science, business, or everyday environment related
to the topic.

The image should be visually specific rather than generic.
Do not use futuristic fantasy imagery or common AI clichés.

Avoid:
- glowing brains
- humanoid robots
- holograms
- generic neural networks
- blue circuit backgrounds
- generic laptops with charts
- cyberpunk imagery
- generic technology stock photography

People may appear naturally, but do not create identifiable real people or portraits.

Absolutely no readable text, letters, logos, labels, captions, watermarks,
or typography anywhere in the image.

Topic:
{topic}

Slide:
{slide_text}
"""


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

    prompt = build_background_prompt(
        topic=topic,
        slide_text=slide_text,
        is_thumbnail=is_thumbnail,
    )

    try:
        background = backend.generate_background(prompt, quality)
    except ImageGenerationBlocked:
        fallback_prompt = build_fallback_background_prompt(
            topic=topic,
            slide_text=slide_text,
            is_thumbnail=is_thumbnail,
        )
        background = backend.generate_background(fallback_prompt, quality)

    if is_thumbnail:
        return template.render_thumbnail(
            background,
            topic,
            style,
        )

    return template.render_content_slide(
        background,
        slide_text,
        index,
        total,
        style,
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

    return [
        compose_single_slide(
            topic=topic,
            slide_text=text,
            index=i,
            total=len(slides),
            is_thumbnail=(i == 0),
            backend=backend,
            quality=quality,
            style=style,
            category_label=label,
        )
        for i, text in enumerate(slides)
    ]