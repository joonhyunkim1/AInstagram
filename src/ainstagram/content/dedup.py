"""임베딩 코사인 유사도로 과거 게시물과의 중복 여부 판단."""
from __future__ import annotations

import numpy as np


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def is_duplicate(
    embedding: list[float], history_embeddings: list[list[float]], threshold: float
) -> bool:
    return any(
        cosine_similarity(embedding, h) >= threshold for h in history_embeddings if h
    )
