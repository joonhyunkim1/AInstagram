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


def test_generate_candidates_retries_on_duplicate_discarded_draft(tmp_path):
    conn = make_conn(tmp_path)
    draft_id = repo.create_draft(
        conn, category=c.CATEGORY_NEWS, topic="old", caption="c",
        slides=["s1"], embedding=[1.0, 0.0, 0.0],
    )
    repo.discard_draft(conn, draft_id)

    dup_cand = {"topic": "dup", "caption": "c1", "slides": ["s1"], "difficulty_level": None}
    new_cand = {"topic": "new", "caption": "c2", "slides": ["s1"], "difficulty_level": None}
    llm = FakeLLM(
        batches=[[dup_cand], [new_cand]],
        embeddings_by_key={
            "dup\nc1": [1.0, 0.0, 0.0],  # 폐기된 draft와 동일 -> 중복
            "new\nc2": [0.0, 1.0, 0.0],
        },
    )

    result = topic_generator.generate_candidates(conn, c.CATEGORY_NEWS, llm, count=1)
    assert len(result) == 1
    assert result[0]["topic"] == "new"


class ContextRecordingFakeLLM:
    """호출마다 넘어온 context를 기록해서, 재시도 시 거절된 주제가 컨텍스트에
    반영되는지 검증하는 데 쓴다."""

    def __init__(self, batches, embeddings_by_key):
        self.batches = list(batches)
        self.embeddings_by_key = embeddings_by_key
        self.contexts: list[str] = []

    def generate_topics(self, category, context, count):
        self.contexts.append(context)
        return self.batches.pop(0)

    def embed(self, text):
        return self.embeddings_by_key[text]


def test_generate_candidates_feeds_rejected_topics_back_into_retry_context(tmp_path):
    conn = make_conn(tmp_path)
    repo.insert_history(
        conn, category=c.CATEGORY_NEWS, topic="old", caption="c", embedding=[1.0, 0.0, 0.0]
    )

    dup_cand = {"topic": "dup topic", "caption": "c1", "slides": ["s1"], "difficulty_level": None}
    new_cand = {"topic": "new topic", "caption": "c2", "slides": ["s1"], "difficulty_level": None}
    llm = ContextRecordingFakeLLM(
        batches=[[dup_cand], [new_cand]],
        embeddings_by_key={
            "dup topic\nc1": [1.0, 0.0, 0.0],  # 기존 이력과 동일 -> 중복
            "new topic\nc2": [0.0, 1.0, 0.0],
        },
    )

    result = topic_generator.generate_candidates(conn, c.CATEGORY_NEWS, llm, count=1)

    assert len(result) == 1
    assert result[0]["topic"] == "new topic"
    assert len(llm.contexts) == 2
    assert "dup topic" not in llm.contexts[0]
    assert "dup topic" in llm.contexts[1]  # 재시도 컨텍스트에 거절된 주제가 반영됨


def test_generate_candidates_retries_on_duplicate_pending_draft(tmp_path):
    """검수 대기 중이거나 채택된(아직 발행/폐기 안 된) draft도 중복 비교 대상에
    포함되어야 한다 - 그렇지 않으면 같은 뉴스가 검수 대기 중에 또 생성된다."""
    conn = make_conn(tmp_path)
    repo.create_draft(
        conn, category=c.CATEGORY_NEWS, topic="pending old", caption="c",
        slides=["s1"], embedding=[1.0, 0.0, 0.0],
    )  # status는 기본값 pending 그대로 둠 (아직 검수 안 함)

    dup_cand = {"topic": "dup", "caption": "c1", "slides": ["s1"], "difficulty_level": None}
    new_cand = {"topic": "new", "caption": "c2", "slides": ["s1"], "difficulty_level": None}
    llm = FakeLLM(
        batches=[[dup_cand], [new_cand]],
        embeddings_by_key={
            "dup\nc1": [1.0, 0.0, 0.0],  # 검수 대기 중인 draft와 동일 -> 중복
            "new\nc2": [0.0, 1.0, 0.0],
        },
    )

    result = topic_generator.generate_candidates(conn, c.CATEGORY_NEWS, llm, count=1)
    assert len(result) == 1
    assert result[0]["topic"] == "new"


def test_generate_candidates_ignores_history_older_than_window_days(tmp_path):
    conn = make_conn(tmp_path)
    repo.insert_history(
        conn, category=c.CATEGORY_NEWS, topic="old", caption="c", embedding=[1.0, 0.0, 0.0]
    )
    draft_id = repo.create_draft(
        conn, category=c.CATEGORY_NEWS, topic="old_discarded", caption="c",
        slides=["s1"], embedding=[0.0, 0.0, 1.0],
    )
    repo.discard_draft(conn, draft_id)
    # window_days(기본 7일)보다 오래된 것처럼 타임스탬프를 과거로 되돌려서
    # 다시 생성될 수 있는지 확인한다.
    conn.execute("UPDATE post_history SET published_at = ?", ("2000-01-01T00:00:00+00:00",))
    conn.execute("UPDATE drafts SET created_at = ?", ("2000-01-01T00:00:00+00:00",))
    conn.commit()

    dup_cand = {"topic": "dup", "caption": "c1", "slides": ["s1"], "difficulty_level": None}
    discarded_dup_cand = {"topic": "dup2", "caption": "c2", "slides": ["s1"], "difficulty_level": None}
    llm = FakeLLM(
        batches=[[dup_cand, discarded_dup_cand]],
        embeddings_by_key={
            "dup\nc1": [1.0, 0.0, 0.0],
            "dup2\nc2": [0.0, 0.0, 1.0],
        },
    )

    result = topic_generator.generate_candidates(conn, c.CATEGORY_NEWS, llm, count=2)
    assert {r["topic"] for r in result} == {"dup", "dup2"}


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


def test_build_caption_with_hashtags_caps_at_seven():
    caption = topic_generator.build_caption_with_hashtags(
        "base caption",
        dynamic_hashtags=["Tag1", "Tag2", "Tag3"],
        fixed_hashtags=["F1", "F2", "F3", "F4", "F5", "F6", "F7"],
    )
    assert caption.count("#") == 7
    # 동적 태그가 우선 채워지고, 남는 자리(4개)는 고정 태그 앞쪽부터 채워짐
    assert "#Tag1" in caption and "#Tag2" in caption and "#Tag3" in caption
    assert "#F1" in caption and "#F4" in caption
    assert "#F5" not in caption


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
