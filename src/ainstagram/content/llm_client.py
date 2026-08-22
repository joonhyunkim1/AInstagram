"""OpenAI 텍스트 생성/임베딩 래퍼.

topic_generator는 이 클래스의 인터페이스(generate_topics/embed)만 의존하므로,
테스트에서는 실제 OpenAI를 호출하지 않는 가짜 객체로 바꿔 끼울 수 있다.
"""
from __future__ import annotations

import json

from openai import OpenAI

from ..config import get_config

SYSTEM_PROMPT = """너는 AI 관련 인스타그램 카드뉴스 계정의 콘텐츠 작가다.
독자는 AI에 관심이 많아서 정보 자체를 원하고, 완전 자동화로 올라오는 콘텐츠라는 점도 어느 정도 감수할 수 있는 사람들이다.
그러니 과한 감성/잡담보다는 정보 밀도와 정확성에 집중해라.

각 게시물은 5~7장의 카드뉴스 슬라이드로 구성된다.
- 1번째 슬라이드는 썸네일: 클릭을 유도하는 한 줄 후킹 문구 (통일된 톤 유지, 자극적 어그로는 금지)
- 2번째 슬라이드부터는 핵심 내용을 순서대로 풀어내기
- 마지막 슬라이드는 요약 또는 정리

반드시 JSON으로만 응답한다."""


def _build_prompt(category: str, context: str, count: int) -> str:
    return f"""카테고리: {category}
참고 컨텍스트:
{context}

서로 겹치지 않는 소재로 {count}개의 후보 게시물을 만들어라.
각 후보는 다음 JSON 형식을 따르는 배열 "candidates"의 원소로 작성한다:
{{
  "topic": "한 줄 주제",
  "caption": "인스타그램 캡션 (해시태그 포함 가능)",
  "slides": ["슬라이드1 텍스트", "슬라이드2 텍스트", "... 5~7개"],
  "difficulty_level": 1~5 사이 정수 또는 null (지식 카테고리가 아니면 null)
}}

최종 출력은 {{"candidates": [...]}} 형태의 JSON 객체 하나여야 한다."""


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
