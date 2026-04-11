from __future__ import annotations

import re
from typing import Any

from kg_agents.config import SIMILARITY_THRESHOLD

from .ontology_loader import get_product_metadata


def _is_likely_non_english(text: str) -> bool:
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return False
    non_ascii = sum(1 for c in alpha if ord(c) > 127)
    return non_ascii / len(alpha) > 0.30


def check_domain_relevance(
    user_message: str,
    top_score: float = 0.0,
    product_meta: dict[str, Any] | None = None,
    model: str | None = None,
) -> str:
    if top_score >= SIMILARITY_THRESHOLD:
        return "relevant"

    if _is_likely_non_english(user_message):
        return "not_relevant"

    meta = product_meta or get_product_metadata()
    topics: list[str] = meta.get("domain_topics", [])
    if topics:
        msg_lower = user_message.lower()
        for topic in topics:
            for word in re.split(r"[\s,/:]+", topic.lower()):
                word = word.strip(".()")
                if len(word) >= 5 and word in msg_lower:
                    return "relevant"

    if len(user_message.strip().split()) < 3:
        return "unclear"

    return "unclear"
