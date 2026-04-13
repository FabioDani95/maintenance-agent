from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI

from kg_agents.config import DEFAULT_EMBEDDINGS_PATH, OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL

from .ontology_loader import OntologyIndex, get_index

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _embed(text: str) -> list[float]:
    response = _get_client().embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


def get_text_embeddings(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    response = _get_client().embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]


def build_symptom_embeddings(index: OntologyIndex | None = None) -> dict[str, Any]:
    active_index = index or get_index()
    result: dict[str, Any] = {}
    for symptom in active_index.symptoms:
        sid = symptom.get("symptom_id", "")
        text = symptom.get("name", "") + ". " + symptom.get("description", "")
        print(f"  Embedding {sid}...")
        result[sid] = _embed(text)
    return result


def save_embeddings(embeddings: dict[str, Any], path: Path | None = None) -> None:
    embeddings_path = path or DEFAULT_EMBEDDINGS_PATH
    with embeddings_path.open("w", encoding="utf-8") as f:
        json.dump(embeddings, f)
    print(f"Saved {len(embeddings)} embeddings to {embeddings_path}")


def load_symptom_embeddings(path: Path | None = None) -> dict[str, list[float]]:
    embeddings_path = path or DEFAULT_EMBEDDINGS_PATH
    if not embeddings_path.exists():
        raise FileNotFoundError(
            f"symptom_embeddings.json not found at {embeddings_path}. "
            "Generate embeddings before starting the chat flow."
        )
    with embeddings_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_query_embedding(text: str) -> list[float]:
    return _embed(text)


def main() -> int:
    print("Building symptom embeddings...")
    embeddings = build_symptom_embeddings()
    save_embeddings(embeddings)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
