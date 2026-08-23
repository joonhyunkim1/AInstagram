"""OpenAI 텍스트 생성/임베딩 래퍼.

topic_generator는 이 클래스의 인터페이스(generate_topics/embed)만 의존하므로,
테스트에서는 실제 OpenAI를 호출하지 않는 가짜 객체로 바꿔 끼울 수 있다.
"""
from __future__ import annotations

import json

from openai import OpenAI

from ..config import get_config

SYSTEM_PROMPT = """You are the content writer for an Instagram account about AI (artificial intelligence),
covering global AI news and educational AI knowledge for a worldwide, English-speaking audience.
Readers are AI enthusiasts who want real information and are fine with fully automated posting as
long as the content is accurate and useful. Prioritize information density and accuracy over
fluff or forced casualness.

Each post is a carousel of slides. Use only as many slides as the content actually needs - a
simple, quick story should be short; only use more slides when there is genuinely enough substance
to justify it. Never pad or stretch content just to hit a higher slide count. Slide text is
rendered as large text directly on top of a photo, so keep each slide to about 3 short sentences -
enough to actually explain the point, but not a long paragraph:
- Slide 1 is the cover: one punchy hook line (about 1 short sentence) that makes people stop
  scrolling. Keep a consistent, non-clickbait tone across posts.
- The middle slides (if any) carry the actual content, in order, about 3 short sentences per slide.
- The last slide wraps up or summarizes, also about 3 short sentences.

The full, detailed write-up goes ONLY in the "caption" field, never in the slide text. When the
category is "news", write the caption like a real news article: lead with the single most important
fact, state what happened and who/what/when clearly, stay factual, and avoid speculation or hype
that isn't supported by the source material. When the category is "knowledge", write the caption
like a clear, well-structured educational explainer.

Always respond in English. Respond with JSON only."""


def _build_prompt(category: str, context: str, count: int, min_slides: int, max_slides: int) -> str:
    return f"""Category: {category}
Reference context:
{context}

Come up with {count} candidate posts on topics that do not overlap with each other.
Each candidate must be an element of a JSON array "candidates", following this shape:
{{
  "topic": "one-line topic",
  "caption": "Full Instagram caption body, written like a real article (do NOT include hashtags here, they are added separately)",
  "slides": ["cover hook, about 1 short sentence", "slide 2 text, about 3 short sentences", "... {min_slides} to {max_slides} items total - use the minimum that fits the content, do not pad"],
  "hashtags": ["5 to 8 topic-specific hashtag words, no # symbol, no spaces, e.g. OpenAI, GPT5, LLM"],
  "difficulty_level": an integer from 1 to 5, or null (null unless category is "knowledge")
}}

The final output must be a single JSON object of the shape {{"candidates": [...]}}."""


class LLMClient:
    def __init__(
        self,
        api_key: str,
        text_model: str,
        embedding_model: str,
        min_slides: int = 2,
        max_slides: int = 6,
    ):
        self._client = OpenAI(api_key=api_key)
        self.text_model = text_model
        self.embedding_model = embedding_model
        self.min_slides = min_slides
        self.max_slides = max_slides

    def generate_topics(self, category: str, context: str, count: int) -> list[dict]:
        response = self._client.chat.completions.create(
            model=self.text_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_prompt(
                        category, context, count, self.min_slides, self.max_slides
                    ),
                },
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        return data["candidates"]

    def embed(self, text: str) -> list[float]:
        response = self._client.embeddings.create(model=self.embedding_model, input=text)
        return response.data[0].embedding

    @classmethod
    def from_config(cls) -> "LLMClient":
        cfg = get_config()
        return cls(
            api_key=cfg.openai_api_key,
            text_model=cfg.image.text_model,
            embedding_model=cfg.image.embedding_model,
            min_slides=cfg.posting.images_min,
            max_slides=cfg.posting.images_max,
        )
