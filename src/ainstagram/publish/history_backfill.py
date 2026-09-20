"""인스타그램 피드를 기준으로 누락된 발행 이력(post_history)을 복구한다.

발행 API가 에러를 반환하는 바람에(게시는 실제로 됨) 이력이 저장되지 않은 게시물을
피드에서 찾아 채워 넣는다. 원본 초안은 DB에 남아있지 않아서 아래처럼 근사한다.

- 주제(topic): 캡션 본문의 첫 문장 (원래 헤드라인은 저장된 적이 없어 복원 불가)
- 카테고리: 호출하는 쪽이 정한다. 이력이 갱신되지 않은 동안 자동 생성 게시물은 매번
  같은 카테고리(마지막 이력의 다음 카테고리)로 뽑혔기 때문에 전부 같은 값을 쓴다.
- 임베딩: 중복 검사와 같은 방식으로 "주제\n본문"을 임베딩 (해시태그 문단은 제외)
- 발행 시각: 인스타그램에 기록된 실제 게시 시각

이력에 마지막으로 기록된 시각 이후의, 아직 등록 안 된 캐러셀만 대상으로 하므로
여러 번 실행해도 중복으로 들어가지 않는다. 제목이 붙기 전(제목 추가 이전) 캡션을 가정한다.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, Iterable

import sqlite3

from .. import repository as repo

CAROUSEL_TYPE = "CAROUSEL_ALBUM"


def _parse_instagram_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)


def _strip_hashtag_paragraphs(caption: str) -> str:
    paragraphs = caption.strip().split("\n\n")
    kept = [
        p for p in paragraphs
        if not (p.split() and all(token.startswith("#") for token in p.split()))
    ]
    return "\n\n".join(kept).strip()


def _first_sentence(text: str, limit: int = 120) -> str:
    first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return first[:limit].rstrip()


def backfill_history(
    conn: sqlite3.Connection,
    media: Iterable[dict],
    embed: Callable[[str], list[float]] | None,
    category: str,
    dry_run: bool = False,
) -> list[dict]:
    """복구한(dry_run이면 복구할) 게시물 목록을 오래된 순으로 반환한다.

    media는 최신순이어야 한다 (InstagramClient.iter_media). dry_run이면 DB를 바꾸지 않고
    임베딩도 계산하지 않으므로 embed는 None이어도 된다.
    """
    history = repo.recent_history(conn, limit=None)
    known_ids = {h.instagram_media_id for h in history if h.instagram_media_id}
    since = max((datetime.fromisoformat(h.published_at) for h in history), default=None)

    candidates: list[tuple[datetime, dict]] = []
    for item in media:
        posted_at = _parse_instagram_timestamp(item["timestamp"])
        if since is not None and posted_at <= since:
            break
        if item.get("media_type") != CAROUSEL_TYPE or item["id"] in known_ids:
            continue
        if not (item.get("caption") or "").strip():
            continue
        candidates.append((posted_at, item))
    candidates.reverse()

    restored = []
    for posted_at, item in candidates:
        body = _strip_hashtag_paragraphs(item["caption"])
        topic = _first_sentence(body)
        if not dry_run:
            if embed is None:
                raise ValueError("dry_run이 아니면 embed 함수가 필요합니다.")
            repo.insert_history(
                conn,
                category=category,
                topic=topic,
                caption=item["caption"],
                instagram_media_id=item["id"],
                embedding=embed(f"{topic}\n{body}"),
                published_at=posted_at.isoformat(),
            )
        restored.append({"id": item["id"], "published_at": posted_at.isoformat(), "topic": topic})
    return restored
