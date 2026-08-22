import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram.content import dedup


def test_cosine_similarity_identical_vectors():
    assert dedup.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_similarity_orthogonal_vectors():
    assert dedup.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_is_duplicate_true_above_threshold():
    history = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    assert dedup.is_duplicate([1.0, 0.0, 0.0], history, threshold=0.85) is True


def test_is_duplicate_false_below_threshold():
    history = [[0.0, 1.0, 0.0]]
    assert dedup.is_duplicate([1.0, 0.0, 0.0], history, threshold=0.85) is False


def test_is_duplicate_ignores_empty_history_entries():
    history = [None, []]
    assert dedup.is_duplicate([1.0, 0.0, 0.0], history, threshold=0.85) is False
