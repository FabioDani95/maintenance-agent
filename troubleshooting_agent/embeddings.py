from __future__ import annotations

"""
Embedding management for symptom nodes.

Offline usage (generate and save):
    python embeddings.py

Runtime usage:
    from embeddings import load_symptom_embeddings, get_query_embedding
"""

import json
import sys
from typing import Any

from openai import OpenAI

from config import (
    EMBEDDINGS_PATH,
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL,
)
from ontology_loader import get_index

_client = OpenAI(api_key=OPENAI_API_KEY)


def _embed(text: str) -> list[float]:
    response = _client.embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


def build_symptom_embeddings() -> dict[str, Any]:
    """Generate embeddings for all Symptom nodes and return as dict."""
    index = get_index()
    result: dict[str, Any] = {}
    for symptom in index.symptoms:
        sid = symptom.get("symptom_id", "")
        text = symptom.get("name", "") + ". " + symptom.get("description", "")
        print(f"  Embedding {sid}...")
        result[sid] = _embed(text)
    return result


def save_embeddings(embeddings: dict[str, Any]) -> None:
    with EMBEDDINGS_PATH.open("w", encoding="utf-8") as f:
        json.dump(embeddings, f)
    print(f"Saved {len(embeddings)} embeddings to {EMBEDDINGS_PATH}")


def load_symptom_embeddings() -> dict[str, list[float]]:
    if not EMBEDDINGS_PATH.exists():
        raise FileNotFoundError(
            f"symptom_embeddings.json not found at {EMBEDDINGS_PATH}. "
            "Run: python embeddings.py"
        )
    with EMBEDDINGS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_query_embedding(text: str) -> list[float]:
    return _embed(text)


if __name__ == "__main__":
    print("Building symptom embeddings...")
    embeddings = build_symptom_embeddings()
    save_embeddings(embeddings)
    print("Done.")
    sys.exit(0)
