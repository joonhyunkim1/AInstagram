"""인스타그램 캡션 길이 제한(2,200자) 처리.

인스타그램은 캡션이 2,200자를 넘으면 컨테이너 생성 단계에서 400 에러
(code 36004, subcode 2207010)로 거절한다. 발행할 때 캡션 앞에 제목을 붙이기 때문에,
제목까지 포함한 최종 길이를 기준으로 본문을 문장 단위로 줄인다.
"""
from __future__ import annotations

import re

INSTAGRAM_CAPTION_LIMIT = 2200
ELLIPSIS = "…"
PARAGRAPH_BREAK = "\n\n"

# 문장 끝: 마침표/물음표/느낌표/말줄임표(+ 닫는 따옴표·괄호) 뒤에 공백이나 글 끝이 오는 위치.
# "3.5"처럼 뒤에 공백이 없는 마침표는 문장 끝으로 보지 않는다.
_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*(?=\s|$)")


def caption_length(text: str) -> int:
    """인스타그램 기준 글자 수를 보수적으로 추정한다 (UTF-16 코드 유닛 수).

    인스타그램이 글자 수를 세는 정확한 방식은 공개돼 있지 않아서, 이모지처럼 한 글자가
    2자로 셀 수 있는 경우까지 감안해 코드포인트 수보다 작지 않은 값으로 센다.
    """
    return len(text.encode("utf-16-le")) // 2


def _paragraphs(*parts: str) -> str:
    return PARAGRAPH_BREAK.join(part for part in parts if part)


def split_hashtags(caption: str) -> tuple[str, list[str]]:
    """캡션 마지막 줄이 해시태그로만 이루어져 있으면 (본문, 태그 목록)으로 나눈다."""
    caption = caption.rstrip()
    body, _, last_line = caption.rpartition("\n")
    tags = last_line.split()
    if tags and all(tag.startswith("#") for tag in tags):
        return body.rstrip(), tags
    return caption, []


def _truncate_words(text: str, budget: int) -> str:
    """단어 경계에서 잘라 말줄임표를 붙인다. 결과는 항상 budget 이하."""
    room = budget - caption_length(ELLIPSIS)
    if room <= 0:
        return ""
    head = text[:room]
    while head and caption_length(head) > room:
        head = head[:-1]
    if len(head) < len(text) and not text[len(head)].isspace():
        # 단어 중간이면 마지막 공백까지 되돌린다 (공백이 전혀 없으면 그냥 자른다)
        space = max(head.rfind(" "), head.rfind("\n"))
        if space > 0:
            head = head[:space]
    head = head.rstrip().rstrip(",;:")
    return head + ELLIPSIS if head else ""


def _trim_body(body: str, budget: int) -> str:
    """본문을 budget 이하로 줄인다. 끝에서부터 문장 단위로 빼고, 첫 문장조차 안 들어가면
    단어 단위로 자른 뒤 말줄임표를 붙인다."""
    if caption_length(body) <= budget:
        return body
    for match in reversed(list(_SENTENCE_END.finditer(body))):
        candidate = body[: match.end()].rstrip()
        if caption_length(candidate) <= budget:
            return candidate
    return _truncate_words(body, budget)


def fit_caption(caption: str, title: str = "", limit: int = INSTAGRAM_CAPTION_LIMIT) -> str:
    """제목 + 빈 줄 + caption이 limit 안에 들어오도록 caption(본문 + 해시태그)을 줄인다.

    반환값에 제목은 포함되지 않는다. 이미 들어오면 그대로 반환하고, 넘치면:
    1. 본문을 끝에서부터 문장 단위로 뺀다 (해시태그는 노출에 중요하고 짧으므로 유지).
    2. 해시태그만으로도 넘치는 극단적인 경우에만 뒤쪽(고정) 태그부터 뺀다.
    """
    title = title.strip()
    budget = limit - (caption_length(title) + caption_length(PARAGRAPH_BREAK) if title else 0)
    if caption_length(caption) <= budget:
        return caption
    if budget <= 0:
        return ""

    body, tags = split_hashtags(caption)
    while tags and caption_length(" ".join(tags)) > budget:
        tags.pop()
    tag_line = " ".join(tags)
    body_budget = budget - (caption_length(tag_line) + caption_length(PARAGRAPH_BREAK) if tag_line else 0)
    body = _trim_body(body, body_budget) if body_budget > 0 else ""
    return _paragraphs(body, tag_line)


def compose_caption(title: str, caption: str, limit: int = INSTAGRAM_CAPTION_LIMIT) -> str:
    """발행용 최종 캡션: 제목, 빈 줄, (필요하면 줄인) 캡션. 결과는 항상 limit 이하."""
    title = title.strip()
    if caption_length(title) > limit:
        return _truncate_words(title, limit)
    return _paragraphs(title, fit_caption(caption, title, limit))
