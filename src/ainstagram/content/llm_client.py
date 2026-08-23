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

Each post is a carousel of 5 to 7 slides:
- Slide 1 is the cover: one punchy hook line that makes people stop scrolling. Keep a consistent,
  non-clickbait tone across posts.
- The middle slides carry the actual content, in order.
- The last slide wraps up or summarizes.

When the category is "news", write like a real news article: lead with the single most important
fact, state what happened and who/what/when clearly, stay factual, and avoid speculation or hype
that isn't supported by the source material.
When the category is "knowledge", write like a clear, well-structured educational explainer.

Always respond in English. Respond with JSON only."""


def _build_prompt(category: str, context: str, count: int) -> str:
    return f"""Category: {category}
Reference context:
{context}

Come up with {count} candidate posts on topics that do not overlap with each other.
Each candidate must be an element of a JSON array "candidates", following this shape:
{{
  "topic": "one-line topic",
  "caption": "Instagram caption body (do NOT include hashtags here, they are added separately)",
  "slides": ["slide 1 text", "slide 2 text", "... 5 to 7 items total"],
  "hashtags": ["5 to 8 topic-specific hashtag words, no # symbol, no spaces, e.g. OpenAI, GPT5, LLM"],
  "difficulty_level": an integer from 1 to 5, or null (null unless category is "knowledge")
}}

The final output must be a single JSON object of the shape {{"candidates": [...]}}."""


class LLMClient:
    def __init__(self, api_key: str, text_model: str, embedding_model: str):
        self._client = OpenAI(api_key=api_key)
        self.text_model = text_model
        self.embedding_model = embedding_model

    def generate_topics(self, category: str, context: str, count: int) -> list[dict]:
        response = self._client.chat.completions.create(
            model=self.text_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_prompt(category, context, count)},
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
        )
