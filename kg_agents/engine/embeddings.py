from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI
import openai.resources  # noqa: F401  # eager import: avoid Py3.14 deadlock under concurrent first access

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
        print(f"  Embedding symptom {sid}...")
        result[sid] = _embed(text)
    return result


def build_failure_mode_embeddings(index: OntologyIndex | None = None) -> dict[str, Any]:
    active_index = index or get_index()
    result: dict[str, Any] = {}
    for fm in active_index.failure_modes:
        fm_id = fm.get("failure_mode_id", "")
        if not fm_id:
            continue
        parts = [
            fm.get("name", ""),
            fm.get("description", ""),
            fm.get("material_context", ""),
        ]
        text = ". ".join(part for part in parts if part)
        print(f"  Embedding failure mode {fm_id}...")
        result[fm_id] = _embed(text)
    return result


def save_embeddings(embeddings: dict[str, Any], path: Path | None = None) -> None:
    """Save embeddings. Accepts either the legacy flat dict (symptoms only) or
    the new sectioned dict {"symptoms": {...}, "failure_modes": {...}}."""
    embeddings_path = path or DEFAULT_EMBEDDINGS_PATH
    with embeddings_path.open("w", encoding="utf-8") as f:
        json.dump(embeddings, f)
    if "symptoms" in embeddings or "failure_modes" in embeddings:
        n_sym = len(embeddings.get("symptoms", {}))
        n_fm = len(embeddings.get("failure_modes", {}))
        print(f"Saved {n_sym} symptom + {n_fm} failure-mode embeddings to {embeddings_path}")
    else:
        print(f"Saved {len(embeddings)} embeddings to {embeddings_path}")


def _read_embeddings_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"embeddings file not found at {path}. "
            "Generate embeddings before starting the chat flow."
        )
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_embeddings(path: Path | None = None) -> dict[str, dict[str, list[float]]]:
    """Load embeddings in the sectioned format.

    Back-compat: if the file on disk is a legacy flat dict keyed by symptom id,
    it is returned as {"symptoms": <flat>, "failure_modes": {}}.
    """
    embeddings_path = path or DEFAULT_EMBEDDINGS_PATH
    data = _read_embeddings_file(embeddings_path)
    if isinstance(data, dict) and ("symptoms" in data or "failure_modes" in data):
        return {
            "symptoms": data.get("symptoms", {}) or {},
            "failure_modes": data.get("failure_modes", {}) or {},
        }
    # Legacy flat format: {symptom_id: [...]}
    return {"symptoms": data or {}, "failure_modes": {}}


def load_symptom_embeddings(path: Path | None = None) -> dict[str, list[float]]:
    """Back-compat helper: returns only the symptom section."""
    return load_embeddings(path).get("symptoms", {})


def get_query_embedding(text: str) -> list[float]:
    return _embed(text)


def main() -> int:
    print("Building symptom embeddings...")
    symptoms = build_symptom_embeddings()
    print("Building failure-mode embeddings...")
    failure_modes = build_failure_mode_embeddings()
    save_embeddings({"symptoms": symptoms, "failure_modes": failure_modes})
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
