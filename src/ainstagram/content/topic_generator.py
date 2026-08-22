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
        return "아직 지식 카테고리로 다룬 주제 없음 - 입문 수준(난이도 1)부터 시작."

    covered = [f"- (난이도 {h.difficulty_level}) {h.topic}" for h in history]
    levels = [h.difficulty_level for h in history if h.difficulty_level is not None]
    avg_level = sum(levels) / len(levels) if levels else 1
    return (
        f"지금까지 다룬 지식 주제 (최근 {len(history)}개, 평균 난이도 {avg_level:.1f}):\n"
        + "\n".join(covered)
        + "\n\n독자 수준을 고려해 위 목록과 겹치지 않는 다음 단계 주제를 골라라."
    )


def _build_context(conn: sqlite3.Connection, category: str, cfg: AppConfig) -> str:
    if category == c.CATEGORY_NEWS:
        return build_news_context(cfg.content.news_feeds)
    return _build_knowledge_context(conn, cfg)


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


def generate_and_store_drafts(
    conn: sqlite3.Connection,
    category: str,
    llm: LLM,
    count: int | None = None,
) -> list[int]:
    candidates = generate_candidates(conn, category, llm, count)
    draft_ids = []
    for cand in candidates:
        draft_id = repo.create_draft(
            conn,
            category=category,
            topic=cand["topic"],
            caption=cand["caption"],
            slides=cand["slides"],
            embedding=cand.get("embedding"),
            difficulty_level=cand.get("difficulty_level"),
        )
        draft_ids.append(draft_id)
    return draft_ids
