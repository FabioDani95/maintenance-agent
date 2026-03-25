from __future__ import annotations

"""
Domain relevance check — fully deterministic, zero LLM calls.

Logic:
1. If top embedding score >= HIGH_CONFIDENCE_THRESHOLD → relevant (embedding already matched).
2. If top embedding score >= SIMILARITY_THRESHOLD      → relevant (marginal but valid match).
3. Keyword scan against domain_topics from ontology    → relevant / unclear.
4. Language heuristic (non-ASCII majority)             → not_relevant.
5. Fallback                                            → unclear.

The `top_score` parameter is the best cosine similarity already computed by
find_top_k_symptoms(). Passing it avoids any redundant work.
"""

import re

from config import HIGH_CONFIDENCE_THRESHOLD, SIMILARITY_THRESHOLD
from ontology_loader import get_product_metadata


def _is_likely_non_english(text: str) -> bool:
    """Rough heuristic: if > 30 % of alphabetic chars are non-ASCII → not English."""
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return False
    non_ascii = sum(1 for c in alpha if ord(c) > 127)
    return non_ascii / len(alpha) > 0.30


def check_domain_relevance(
    user_message: str,
    top_score: float = 0.0,
    # kept for backward-compat but ignored — no LLM call
    model: str | None = None,
) -> str:
    """Return 'relevant', 'unclear', or 'not_relevant'. No LLM call."""

    # Already matched by embeddings
    if top_score >= SIMILARITY_THRESHOLD:
        return "relevant"

    # Non-English heuristic
    if _is_likely_non_english(user_message):
        return "not_relevant"

    # Keyword scan against ontology domain_topics
    meta = get_product_metadata()
    topics: list[str] = meta.get("domain_topics", [])
    if topics:
        msg_lower = user_message.lower()
        for topic in topics:
            # match any token from the topic phrase as substring in the message
            for word in re.split(r"[\s,/:]+", topic.lower()):
                word = word.strip(".()")
                if len(word) >= 5 and word in msg_lower:
                    return "relevant"

    # Too short or too vague
    if len(user_message.strip().split()) < 3:
        return "unclear"

    return "unclear"
