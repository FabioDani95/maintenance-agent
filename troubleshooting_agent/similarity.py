from __future__ import annotations

import numpy as np

from config import HIGH_CONFIDENCE_THRESHOLD, SIMILARITY_THRESHOLD, TOP_K_SYMPTOMS


def _cosine(a: list[float], b: list[float]) -> float:
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def find_top_k_symptoms(
    query_embedding: list[float],
    symptom_embeddings: dict[str, list[float]],
    k: int = TOP_K_SYMPTOMS,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[tuple[str, float]]:
    """
    Return up to k (symptom_id, score) pairs sorted by descending score.
    Returns empty list if the best score is below threshold.
    """
    scores = [
        (sid, _cosine(query_embedding, emb))
        for sid, emb in symptom_embeddings.items()
    ]
    scores.sort(key=lambda x: x[1], reverse=True)
    top = scores[:k]

    if not top or top[0][1] < threshold:
        return []

    return [(sid, score) for sid, score in top if score >= threshold]


def is_high_confidence(scores: list[tuple[str, float]]) -> bool:
    """True when the best match is clearly above the high-confidence threshold."""
    return bool(scores) and scores[0][1] >= HIGH_CONFIDENCE_THRESHOLD
