import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram import constants as c
from ainstagram import repository as repo
from ainstagram.content import topic_generator
from ainstagram.db import get_connection


def make_conn(tmp_path):
    return get_connection(tmp_path / "test.db")


class FakeLLM:
    def __init__(self, batches, embeddings_by_key):
        self.batches = list(batches)
        self.embeddings_by_key = embeddings_by_key

    def generate_topics(self, category, context, count):
        return self.batches.pop(0)

    def embed(self, text):
        return self.embeddings_by_key[text]


def test_generate_candidates_accepts_unique_topics(tmp_path):
    conn = make_conn(tmp_path)
    cand1 = {"topic": "t1", "caption": "c1", "slides": ["s1"], "difficulty_level": None}
    cand2 = {"topic": "t2", "caption": "c2", "slides": ["s1"], "difficulty_level": None}
    llm = FakeLLM(
        batches=[[cand1, cand2]],
        embeddings_by_key={"t1\nc1": [1.0, 0.0], "t2\nc2": [0.0, 1.0]},
    )

    result = topic_generator.generate_candidates(conn, c.CATEGORY_NEWS, llm, count=2)
    assert {r["topic"] for r in result} == {"t1", "t2"}


def test_generate_candidates_retries_on_duplicate(tmp_path):
    conn = make_conn(tmp_path)
    repo.insert_history(
        conn, category=c.CATEGORY_NEWS, topic="old", caption="c", embedding=[1.0, 0.0, 0.0]
    )

    dup_cand = {"topic": "dup", "caption": "c1", "slides": ["s1"], "difficulty_level": None}
    new_cand = {"topic": "new", "caption": "c2", "slides": ["s1"], "difficulty_level": None}
    llm = FakeLLM(
        batches=[[dup_cand], [new_cand]],
        embeddings_by_key={
            "dup\nc1": [1.0, 0.0, 0.0],  # 기존 이력과 동일 -> 중복
            "new\nc2": [0.0, 1.0, 0.0],
        },
    )

    result = topic_generator.generate_candidates(conn, c.CATEGORY_NEWS, llm, count=1)
    assert len(result) == 1
    assert result[0]["topic"] == "new"


def test_generate_and_store_drafts_persists_rows(tmp_path):
    conn = make_conn(tmp_path)
    cand = {
        "topic": "t1",
        "caption": "c1",
        "slides": ["s1", "s2"],
        "hashtags": ["OpenAI", "GPT5"],
        "difficulty_level": 2,
    }
    llm = FakeLLM(batches=[[cand]], embeddings_by_key={"t1\nc1": [1.0, 0.0]})

    draft_ids = topic_generator.generate_and_store_drafts(
        conn, c.CATEGORY_KNOWLEDGE, llm, count=1
    )
    assert len(draft_ids) == 1

    draft = repo.get_draft(conn, draft_ids[0])
    assert draft.topic == "t1"
    assert draft.difficulty_level == 2
    assert draft.slides == ["s1", "s2"]
    assert draft.caption.startswith("c1")
    assert "#OpenAI" in draft.caption
    assert "#GPT5" in draft.caption
    assert "#AI" in draft.caption  # 고정 해시태그도 붙어야 함


def test_generate_and_store_drafts_works_without_hashtags_field(tmp_path):
    conn = make_conn(tmp_path)
    cand = {"topic": "t1", "caption": "c1", "slides": ["s1"], "difficulty_level": None}
    llm = FakeLLM(batches=[[cand]], embeddings_by_key={"t1\nc1": [1.0, 0.0]})

    draft_ids = topic_generator.generate_and_store_drafts(conn, c.CATEGORY_NEWS, llm, count=1)

    draft = repo.get_draft(conn, draft_ids[0])
    assert draft.caption.startswith("c1")
    assert "#AI" in draft.caption  # 고정 해시태그는 여전히 붙음


def test_build_caption_with_hashtags_dedupes_case_insensitively():
    caption = topic_generator.build_caption_with_hashtags(
        "base caption",
        dynamic_hashtags=["OpenAI", "ai"],
        fixed_hashtags=["AI", "MachineLearning"],
    )
    assert caption.startswith("base caption")
    assert caption.count("#") == 3  # OpenAI, ai(먼저 나온 형태 유지), MachineLearning
    assert "#OpenAI" in caption
    assert "#MachineLearning" in caption


def test_build_caption_with_hashtags_no_tags_returns_caption_unchanged():
    caption = topic_generator.build_caption_with_hashtags("base caption", [], [])
    assert caption == "base caption"


def test_build_knowledge_context_reflects_history(tmp_path):
    conn = make_conn(tmp_path)
    repo.insert_history(
        conn, category=c.CATEGORY_KNOWLEDGE, topic="Transformer basics", caption="c",
        difficulty_level=2, embedding=[0.1, 0.2],
    )
    from ainstagram.config import get_config

    context = topic_generator._build_knowledge_context(conn, get_config())
    assert "Transformer basics" in context
    assert "difficulty" in context
