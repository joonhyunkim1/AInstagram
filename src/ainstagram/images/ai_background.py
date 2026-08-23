"""gpt-image로 슬라이드 배경 일러스트만 생성한다 (텍스트/레이아웃은 template.py 담당)."""
from __future__ import annotations

import base64
import io
from typing import Protocol

import openai
from PIL import Image
from openai import OpenAI

from ..config import get_config


class ImageGenerationBlocked(RuntimeError):
    """OpenAI 안전 필터가 이미지 생성을 막았을 때. 민감한 뉴스 주제에서 발생할 수 있다."""


class ImageBackend(Protocol):
    def generate_background(
        self, prompt: str, quality: str, size: str = "1024x1024"
    ) -> Image.Image: ...


class OpenAIImageBackend:
    def __init__(self, api_key: str, model: str):
        self._client = OpenAI(api_key=api_key)
        self.model = model

    def generate_background(
        self, prompt: str, quality: str, size: str = "1024x1024"
    ) -> Image.Image:
        try:
            response = self._client.images.generate(
                model=self.model, prompt=prompt, size=size, quality=quality, n=1
            )
        except openai.BadRequestError as e:
            if getattr(e, "code", None) == "moderation_blocked" or "moderation_blocked" in str(e):
                raise ImageGenerationBlocked(str(e)) from e
            raise
        b64 = response.data[0].b64_json
        return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")

    @classmethod
    def from_config(cls) -> "OpenAIImageBackend":
        cfg = get_config()
        return cls(api_key=cfg.openai_api_key, model=cfg.image.image_model)
