import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram import constants as c
from ainstagram import repository as repo
from ainstagram.db import get_connection
from ainstagram.publish.history_backfill import backfill_history


def make_conn(tmp_path):
    return get_connection(tmp_path / "test.db")


def post(media_id, timestamp, caption, media_type="CAROUSEL_ALBUM"):
    return {"id": media_id, "timestamp": timestamp, "caption": caption, "media_type": media_type}


def seed_last_history(conn):
    repo.insert_history(
        conn, category=c.CATEGORY_KNOWLEDGE, topic="이미 기록됨", caption="c",
        instagram_media_id="known-1", published_at="2026-08-29T15:24:34+00:00",
    )


class RecordingEmbed:
    def __init__(self):
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        return [1.0, 0.0]


def feed():
    """최신순 피드."""
    return [
        post("new-2", "2026-09-02T10:00:00+0000", "Second story. More detail here.\n\n#AI #News"),
        post("new-1", "2026-08-30T09:30:00+0000", "First story headline. Body text follows.\n\n#Tag"),
        post("known-1", "2026-08-29T15:24:29+0000", "이미 기록된 게시물."),
        post("old-manual", "2026-08-01T00:00:00+0000", "이력 이전에 올린 게시물."),
    ]


def test_backfill_inserts_missing_posts_oldest_first_with_real_timestamps(tmp_path):
    conn = make_conn(tmp_path)
    seed_last_history(conn)
    embed = RecordingEmbed()

    restored = backfill_history(conn, feed(), embed, category=c.CATEGORY_NEWS)

    assert [r["id"] for r in restored] == ["new-1", "new-2"]
    history = {h.instagram_media_id: h for h in repo.recent_history(conn, limit=None)}
    assert history["new-1"].published_at == "2026-08-30T09:30:00+00:00"  # 실제 게시 시각
    assert history["new-1"].category == c.CATEGORY_NEWS
    assert history["new-1"].topic == "First story headline."
    assert history["new-1"].embedding == [1.0, 0.0]
    assert history["new-2"].caption.endswith("#AI #News")  # 원본 캡션은 그대로 저장
    # 임베딩은 중복 검사와 같은 방식(주제 + 본문)이고 해시태그 문단은 제외
    assert embed.texts[0] == "First story headline.\nFirst story headline. Body text follows."


def test_backfill_skips_known_non_carousel_and_older_than_last_history(tmp_path):
    conn = make_conn(tmp_path)
    seed_last_history(conn)
    media = [
        post("story-like", "2026-09-03T00:00:00+0000", "이미지 단일 게시물.", media_type="IMAGE"),
        post("no-caption", "2026-09-02T00:00:00+0000", ""),
    ] + feed()

    restored = backfill_history(conn, media, RecordingEmbed(), category=c.CATEGORY_NEWS)

    assert [r["id"] for r in restored] == ["new-1", "new-2"]
    assert repo.recent_history(conn, limit=None)[0].published_at.startswith("2026-09-02")


def test_backfill_is_idempotent(tmp_path):
    conn = make_conn(tmp_path)
    seed_last_history(conn)

    backfill_history(conn, feed(), RecordingEmbed(), category=c.CATEGORY_NEWS)
    second = backfill_history(conn, feed(), RecordingEmbed(), category=c.CATEGORY_NEWS)

    assert second == []
    assert len(repo.recent_history(conn, limit=None)) == 3


def test_backfill_dry_run_reports_without_writing_or_embedding(tmp_path):
    conn = make_conn(tmp_path)
    seed_last_history(conn)

    restored = backfill_history(conn, feed(), None, category=c.CATEGORY_NEWS, dry_run=True)

    assert [r["id"] for r in restored] == ["new-1", "new-2"]
    assert len(repo.recent_history(conn, limit=None)) == 1


def test_backfill_with_empty_history_takes_all_carousels(tmp_path):
    conn = make_conn(tmp_path)

    restored = backfill_history(conn, feed(), RecordingEmbed(), category=c.CATEGORY_NEWS)

    assert len(restored) == 4
