"""카테고리별 컨텍스트를 만들고 LLM으로 후보를 생성한 뒤, 과거 게시물과 겹치면 걸러서
drafts 테이블에 저장한다.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Protocol

from .. import constants as c
from .. import repository as repo
from ..config import AppConfig, get_config
from . import dedup
from .news_sources import build_news_context


class LLM(Protocol):
    def generate_topics(self, category: str, context: str, count: int) -> list[dict[str, Any]]: ...
    def embed(self, text: str) -> list[float]: ...


def _build_knowledge_context(conn: sqlite3.Connection, cfg: AppConfig) -> str:
    history = repo.recent_history(
        conn, category=c.CATEGORY_KNOWLEDGE, limit=cfg.content.dedup.history_window
    )
    if not history:
        return "No knowledge-category topics covered yet - start from an introductory level (difficulty 1)."

    covered = [f"- (difficulty {h.difficulty_level}) {h.topic}" for h in history]
    levels = [h.difficulty_level for h in history if h.difficulty_level is not None]
    avg_level = sum(levels) / len(levels) if levels else 1
    return (
        f"Knowledge topics covered so far (last {len(history)}, average difficulty {avg_level:.1f}):\n"
        + "\n".join(covered)
        + "\n\nConsidering the reader's level, pick the next-step topic that doesn't overlap with the list above."
    )


def _build_context(conn: sqlite3.Connection, category: str, cfg: AppConfig) -> str:
    if category == c.CATEGORY_NEWS:
        return build_news_context(cfg.content.news_feeds)
    return _build_knowledge_context(conn, cfg)


def pick_next_category(conn: sqlite3.Connection, cfg: AppConfig) -> str:
    """가장 최근에 다루지 않은 카테고리를 고르는 단순 라운드로빈."""
    categories = cfg.content.categories
    if len(categories) <= 1:
        return categories[0]

    history = repo.recent_history(conn, limit=1)
    if not history:
        return categories[0]

    last_category = history[0].category
    remaining = [cat for cat in categories if cat != last_category]
    return remaining[0] if remaining else categories[0]


def generate_candidates(
    conn: sqlite3.Connection,
    category: str,
    llm: LLM,
    count: int | None = None,
    max_attempts: int = 3,
) -> list[dict[str, Any]]:
    """중복을 걸러낸 후보 목록을 반환한다 (아직 DB에 저장하지 않음)."""
    cfg = get_config()
    count = count or cfg.review.draft_candidates
    context = _build_context(conn, category, cfg)

    history = repo.recent_history(conn, category=category, limit=cfg.content.dedup.history_window)
    history_embeddings = [h.embedding for h in history if h.embedding]

    accepted: list[dict[str, Any]] = []
    attempts = 0
    while len(accepted) < count and attempts < max_attempts:
        attempts += 1
        need = count - len(accepted)
        raw_candidates = llm.generate_topics(category, context, need)
        for cand in raw_candidates:
            embedding = llm.embed(f"{cand['topic']}\n{cand['caption']}")
            if dedup.is_duplicate(embedding, history_embeddings, cfg.content.dedup.similarity_threshold):
                continue
            cand = dict(cand)
            cand["embedding"] = embedding
            accepted.append(cand)
            history_embeddings.append(embedding)
            if len(accepted) >= count:
                break
    return accepted


def build_caption_with_hashtags(
    base_caption: str, dynamic_hashtags: list[str], fixed_hashtags: list[str]
) -> str:
    """동적(주제별) 해시태그를 앞에, 고정 해시태그를 뒤에 붙인다. 중복은 한 번만 남긴다."""
    seen: set[str] = set()
    ordered_tags: list[str] = []
    for tag in [*dynamic_hashtags, *fixed_hashtags]:
        normalized = tag.lstrip("#").replace(" ", "")
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        ordered_tags.append(normalized)

    if not ordered_tags:
        return base_caption

    tag_line = " ".join(f"#{tag}" for tag in ordered_tags)
    return f"{base_caption}\n\n{tag_line}"


def generate_and_store_drafts(
    conn: sqlite3.Connection,
    category: str,
    llm: LLM,
    count: int | None = None,
) -> list[int]:
    cfg = get_config()
    candidates = generate_candidates(conn, category, llm, count)
    draft_ids = []
    for cand in candidates:
        caption = build_caption_with_hashtags(
            cand["caption"], cand.get("hashtags", []), cfg.content.fixed_hashtags
        )
        draft_id = repo.create_draft(
            conn,
            category=category,
            topic=cand["topic"],
            caption=caption,
            slides=cand["slides"],
            embedding=cand.get("embedding"),
            difficulty_level=cand.get("difficulty_level"),
        )
        draft_ids.append(draft_id)
    return draft_ids
