import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram import constants as c
from ainstagram import repository as repo
from ainstagram.db import get_connection


def make_conn(tmp_path):
    return get_connection(tmp_path / "test.db")


def test_draft_lifecycle(tmp_path):
    conn = make_conn(tmp_path)
    draft_id = repo.create_draft(
        conn,
        category=c.CATEGORY_NEWS,
        topic="GPT-5 출시",
        caption="캡션",
        slides=[{"text": "슬라이드1"}],
        embedding=[0.1, 0.2, 0.3],
    )

    pending = repo.list_pending_drafts(conn)
    assert len(pending) == 1
    assert pending[0].id == draft_id
    assert pending[0].status == c.DRAFT_PENDING

    repo.approve_draft(conn, draft_id, priority=1)
    assert repo.list_pending_drafts(conn) == []

    draft = repo.get_draft(conn, draft_id)
    assert draft.status == c.DRAFT_APPROVED
    assert draft.priority == 1


def test_set_draft_images_persists_urls(tmp_path):
    conn = make_conn(tmp_path)
    draft_id = repo.create_draft(
        conn, category=c.CATEGORY_NEWS, topic="t1", caption="c1", slides=["s1"]
    )
    assert repo.get_draft(conn, draft_id).image_urls is None

    repo.set_draft_images(conn, draft_id, ["url1", "url2"])

    draft = repo.get_draft(conn, draft_id)
    assert draft.image_urls == ["url1", "url2"]


def test_discard_draft(tmp_path):
    conn = make_conn(tmp_path)
    draft_id = repo.create_draft(
        conn, category=c.CATEGORY_KNOWLEDGE, topic="topic", caption="caption", slides=[]
    )
    repo.discard_draft(conn, draft_id)
    draft = repo.get_draft(conn, draft_id)
    assert draft.status == c.DRAFT_DISCARDED
    assert repo.list_pending_drafts(conn) == []


def test_queue_ordering(tmp_path):
    conn = make_conn(tmp_path)
    d1 = repo.create_draft(conn, category=c.CATEGORY_NEWS, topic="t1", caption="c1", slides=[])
    d2 = repo.create_draft(conn, category=c.CATEGORY_NEWS, topic="t2", caption="c2", slides=[])

    repo.enqueue(conn, d1, "c1", ["url1"], priority=5)
    q2_id = repo.enqueue(conn, d2, "c2", ["url2"], priority=1)

    nxt = repo.next_in_queue(conn)
    assert nxt.id == q2_id  # 우선순위 낮은 값이 먼저 나옴

    repo.mark_queue_item_published(conn, q2_id)
    nxt2 = repo.next_in_queue(conn)
    assert nxt2.draft_id == d1


def test_history_recent(tmp_path):
    conn = make_conn(tmp_path)
    repo.insert_history(
        conn, category=c.CATEGORY_NEWS, topic="old news", caption="c",
        instagram_media_id="123", embedding=[0.1, 0.2],
    )
    history = repo.recent_history(conn, category=c.CATEGORY_NEWS)
    assert len(history) == 1
    assert history[0].topic == "old news"
    assert history[0].embedding == [0.1, 0.2]


def test_list_queue_orders_by_priority(tmp_path):
    conn = make_conn(tmp_path)
    d1 = repo.create_draft(conn, category=c.CATEGORY_NEWS, topic="t1", caption="c1", slides=[])
    d2 = repo.create_draft(conn, category=c.CATEGORY_NEWS, topic="t2", caption="c2", slides=[])

    q1 = repo.enqueue(conn, d1, "c1", ["url1"], priority=50)
    q2 = repo.enqueue(conn, d2, "c2", ["url2"], priority=10)

    items = repo.list_queue(conn)
    assert [i.id for i in items] == [q2, q1]


def test_list_queue_excludes_published(tmp_path):
    conn = make_conn(tmp_path)
    d1 = repo.create_draft(conn, category=c.CATEGORY_NEWS, topic="t1", caption="c1", slides=[])
    q1 = repo.enqueue(conn, d1, "c1", ["url1"], priority=10)
    repo.mark_queue_item_published(conn, q1)

    assert repo.list_queue(conn) == []


def test_bump_to_front_with_existing_items(tmp_path):
    conn = make_conn(tmp_path)
    d1 = repo.create_draft(conn, category=c.CATEGORY_NEWS, topic="t1", caption="c1", slides=[])
    d2 = repo.create_draft(conn, category=c.CATEGORY_NEWS, topic="t2", caption="c2", slides=[])
    repo.enqueue(conn, d1, "c1", ["url1"], priority=10)
    q2 = repo.enqueue(conn, d2, "c2", ["url2"], priority=20)

    repo.bump_to_front(conn, q2)

    items = repo.list_queue(conn)
    assert items[0].id == q2


def test_bump_to_front_on_empty_queue_defaults_to_zero(tmp_path):
    conn = make_conn(tmp_path)
    d1 = repo.create_draft(conn, category=c.CATEGORY_NEWS, topic="t1", caption="c1", slides=[])
    q1 = repo.enqueue(conn, d1, "c1", ["url1"], priority=100)

    repo.bump_to_front(conn, q1)

    items = repo.list_queue(conn)
    assert items[0].priority == 0


def test_cancel_queue_item_removes_and_discards_draft(tmp_path):
    conn = make_conn(tmp_path)
    draft_id = repo.create_draft(conn, category=c.CATEGORY_NEWS, topic="t1", caption="c1", slides=[])
    queue_id = repo.enqueue(conn, draft_id, "c1", ["url1"], priority=10)

    repo.cancel_queue_item(conn, queue_id)

    assert repo.list_queue(conn) == []
    draft = repo.get_draft(conn, draft_id)
    assert draft.status == c.DRAFT_DISCARDED
