"""config.yaml + .env를 읽어 전역 설정 객체를 만든다."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "config" / "config.yaml"

load_dotenv(ROOT_DIR / ".env")


@dataclass
class InstagramConfig:
    account_name: str
    business_account_id: str
    access_token: str = field(default_factory=lambda: os.getenv("IG_ACCESS_TOKEN", ""))


@dataclass
class PostingConfig:
    timezone: str
    times: list[str]
    images_min: int
    images_max: int


@dataclass
class DedupConfig:
    similarity_threshold: float
    history_window: int
    window_days: int


@dataclass
class ContentConfig:
    categories: list[str]
    topics: list[str]
    language: str
    news_feeds: list[str]
    dedup: DedupConfig
    fixed_hashtags: list[str]


@dataclass
class ImageQualityConfig:
    draft_preview: str
    final: str


@dataclass
class FontConfig:
    regular: str
    bold: str


@dataclass
class BrandConfig:
    primary_color: str
    overlay_opacity: int
    canvas_size: tuple[int, int]


@dataclass
class ImageConfig:
    text_model: str
    embedding_model: str
    image_model: str
    quality: ImageQualityConfig
    thumbnail_style: str
    font: FontConfig
    brand: BrandConfig


@dataclass
class ReviewConfig:
    draft_candidates: int


@dataclass
class AppConfig:
    instagram: InstagramConfig
    posting: PostingConfig
    content: ContentConfig
    image: ImageConfig
    review: ReviewConfig
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    instagram = InstagramConfig(
        account_name=raw["instagram"]["account_name"],
        business_account_id=raw["instagram"].get("business_account_id", ""),
    )
    posting = PostingConfig(
        timezone=raw["posting"]["timezone"],
        times=raw["posting"]["times"],
        images_min=raw["posting"]["images_per_post"]["min"],
        images_max=raw["posting"]["images_per_post"]["max"],
    )
    content = ContentConfig(
        categories=raw["content"]["categories"],
        topics=raw["content"].get("topics", []),
        language=raw["content"].get("language", "en"),
        news_feeds=raw["content"].get("news_feeds", []),
        dedup=DedupConfig(
            similarity_threshold=raw["content"]["dedup"]["similarity_threshold"],
            history_window=raw["content"]["dedup"]["history_window"],
            window_days=raw["content"]["dedup"].get("window_days", 7),
        ),
        fixed_hashtags=raw["content"].get("fixed_hashtags", []),
    )
    image = ImageConfig(
        text_model=raw["image"]["text_model"],
        embedding_model=raw["image"]["embedding_model"],
        image_model=raw["image"]["image_model"],
        quality=ImageQualityConfig(
            draft_preview=raw["image"]["quality"]["draft_preview"],
            final=raw["image"]["quality"]["final"],
        ),
        thumbnail_style=raw["image"]["thumbnail_style"],
        font=FontConfig(
            regular=raw["image"]["font"]["regular"],
            bold=raw["image"]["font"]["bold"],
        ),
        brand=BrandConfig(
            primary_color=raw["image"]["brand"]["primary_color"],
            overlay_opacity=raw["image"]["brand"]["overlay_opacity"],
            canvas_size=tuple(raw["image"]["brand"]["canvas_size"]),
        ),
    )
    review = ReviewConfig(draft_candidates=raw["review"]["draft_candidates"])

    return AppConfig(
        instagram=instagram,
        posting=posting,
        content=content,
        image=image,
        review=review,
    )


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config
