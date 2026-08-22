import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram import constants as c
from ainstagram import repository as repo
from ainstagram.config import get_config
from ainstagram.content.topic_generator import pick_next_category
from ainstagram.db import get_connection


def make_conn(tmp_path):
    return get_connection(tmp_path / "test.db")


def test_pick_next_category_defaults_to_first_when_no_history(tmp_path):
    conn = make_conn(tmp_path)
    cfg = get_config()
    assert pick_next_category(conn, cfg) == cfg.content.categories[0]


def test_pick_next_category_avoids_last_used_category(tmp_path):
    conn = make_conn(tmp_path)
    cfg = get_config()
    repo.insert_history(conn, category=c.CATEGORY_NEWS, topic="t", caption="c")

    assert pick_next_category(conn, cfg) != c.CATEGORY_NEWS


def test_get_set_state_roundtrip(tmp_path):
    conn = make_conn(tmp_path)
    assert repo.get_state(conn, "offset") is None

    repo.set_state(conn, "offset", "42")
    assert repo.get_state(conn, "offset") == "42"

    repo.set_state(conn, "offset", "43")
    assert repo.get_state(conn, "offset") == "43"
