import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram.content.caption import (
    ELLIPSIS,
    INSTAGRAM_CAPTION_LIMIT,
    caption_length,
    compose_caption,
    fit_caption,
    split_hashtags,
)

TITLE = "OpenAI ships a new reasoning model with a 1M-token context window"
TAGS = "#AI #OpenAI #LLM #Reasoning #ArtificialIntelligence #MachineLearning #TechNews"


def sentence(i):
    return f"Sentence number {i} explains one more detail about the model release in plain words."


def long_body(sentences=30, per_paragraph=5):
    paragraphs = []
    for start in range(0, sentences, per_paragraph):
        paragraphs.append(" ".join(sentence(i) for i in range(start, min(start + per_paragraph, sentences))))
    return "\n\n".join(paragraphs)


def test_caption_within_limit_is_unchanged():
    caption = f"Short body.\n\n{TAGS}"
    assert fit_caption(caption, title=TITLE) == caption
    assert compose_caption(TITLE, caption) == f"{TITLE}\n\n{caption}"


def test_compose_without_title_returns_caption_only():
    assert compose_caption("", "Body.") == "Body."
    assert compose_caption("  Title  ", "Body.") == "Title\n\nBody."


def test_over_limit_caption_keeps_title_and_hashtags_and_cuts_at_sentence_end():
    body = long_body(sentences=30)
    caption = f"{body}\n\n{TAGS}"
    assert caption_length(f"{TITLE}\n\n{caption}") > INSTAGRAM_CAPTION_LIMIT  # 재현: 2,500자대

    result = compose_caption(TITLE, caption)

    assert caption_length(result) <= INSTAGRAM_CAPTION_LIMIT
    assert result.startswith(f"{TITLE}\n\n")
    assert result.endswith(f"\n\n{TAGS}")
    kept_body, tags = split_hashtags(result[len(TITLE) + 2 :])
    assert " ".join(tags) == TAGS
    assert body.startswith(kept_body)
    assert kept_body.endswith("plain words.")  # 문장 중간이 아니라 문장 끝에서 잘림
    assert ELLIPSIS not in kept_body


def test_trimming_keeps_as_much_body_as_fits():
    body = long_body(sentences=30)
    caption = f"{body}\n\n{TAGS}"

    result = compose_caption(TITLE, caption)

    # 한 문장만 더 넣었어도 제한을 넘었어야 한다 (필요 이상으로 줄이지 않음)
    kept_body, _ = split_hashtags(result[len(TITLE) + 2 :])
    next_sentence = sentence(kept_body.count("plain words."))
    assert caption_length(result) + len(" " + next_sentence) > INSTAGRAM_CAPTION_LIMIT


def test_fit_caption_accounts_for_title_length():
    caption = f"{long_body(sentences=30)}\n\n{TAGS}"
    short = fit_caption(caption, title="x" * 300)
    longer = fit_caption(caption, title="x")
    assert caption_length(short) <= INSTAGRAM_CAPTION_LIMIT - 302
    assert caption_length(longer) > caption_length(short)


def test_decimal_points_are_not_sentence_boundaries():
    body = "Model 3.5 scored 92.4 on the benchmark and " + "then kept going " * 200 + "until the end."
    result = fit_caption(body, limit=100)
    assert caption_length(result) <= 100
    assert not result.endswith("3.") and not result.endswith("92.")
    assert result.endswith(ELLIPSIS)


def test_no_sentence_boundary_falls_back_to_word_boundary_with_ellipsis():
    body = "word " * 1000
    result = fit_caption(f"{body.strip()}\n\n{TAGS}", title=TITLE)

    assert caption_length(compose_caption(TITLE, f"{body.strip()}\n\n{TAGS}")) <= INSTAGRAM_CAPTION_LIMIT
    kept_body, tags = split_hashtags(result)
    assert " ".join(tags) == TAGS
    assert kept_body.endswith("word" + ELLIPSIS)  # 단어 중간에서 자르지 않음


def test_hashtags_are_dropped_from_the_end_only_when_they_cannot_fit():
    caption = f"Body sentence.\n\n{TAGS}"
    result = fit_caption(caption, limit=30)

    assert caption_length(result) <= 30
    _, tags = split_hashtags(result)
    assert tags == TAGS.split()[: len(tags)]  # 앞쪽(주제별) 태그가 남는다
    assert len(tags) < len(TAGS.split())


def test_emoji_counts_as_two_characters():
    assert caption_length("🚀") == 2
    assert caption_length("가") == 1
    body = "🚀" * 1500  # 코드포인트로는 1,500자지만 UTF-16으로는 3,000
    assert caption_length(compose_caption("T", body)) <= INSTAGRAM_CAPTION_LIMIT


def test_title_longer_than_limit_is_truncated():
    title = "word " * 1000
    result = compose_caption(title, "Body.")
    assert caption_length(result) <= INSTAGRAM_CAPTION_LIMIT
    assert result.endswith(ELLIPSIS)


def test_split_hashtags_without_tag_line():
    assert split_hashtags("Body.\n\nMore body.") == ("Body.\n\nMore body.", [])
    assert split_hashtags("Body.\n\n#a #b") == ("Body.", ["#a", "#b"])
    assert split_hashtags("Body with #inline tag.") == ("Body with #inline tag.", [])


def test_result_never_exceeds_limit_for_many_shapes():
    for sentences in range(0, 60, 3):
        for title_len in (0, 10, 80, 200):
            title = "T" * title_len
            caption = f"{long_body(sentences)}\n\n{TAGS}" if sentences else TAGS
            assert caption_length(compose_caption(title, caption)) <= INSTAGRAM_CAPTION_LIMIT
