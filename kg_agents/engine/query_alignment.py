from __future__ import annotations

import re
from typing import Any

from kg_agents.config import QUERY_ALIGNMENT_MAX_TERM_FREQUENCY

from .embeddings import get_text_embeddings
from .ontology_loader import OntologyIndex
from .similarity import cosine_similarity

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MIN_TERM_LENGTH = 4
_GROUP_NODE_ID_KEYS = ("symptom_id", "failure_mode_id", "component_id", "error_code_id")
_TOKEN_ALIASES = {
    "axes": "mechanical",
    "axis": "mechanical",
    "bearing": "mechanical",
    "bearings": "mechanical",
    "brake": "mechanical",
    "brakes": "mechanical",
    "breaker": "power",
    "burn": "thermal",
    "burns": "thermal",
    "communicate": "communication",
    "communicating": "communication",
    "communication": "communication",
    "computer": "computer",
    "connect": "communication",
    "connected": "communication",
    "connecting": "communication",
    "connection": "communication",
    "contactors": "power",
    "contactor": "power",
    "control": "control",
    "controller": "control",
    "cooling": "thermal",
    "disk": "software",
    "display": "display",
    "earth": "power",
    "ethernet": "communication",
    "event": "event",
    "fault": "power",
    "fieldbus": "communication",
    "fieldbuses": "communication",
    "firmware": "software",
    "flexpendant": "input",
    "fuse": "power",
    "gearbox": "mechanical",
    "gearboxes": "mechanical",
    "grease": "mechanical",
    "heat": "thermal",
    "holding": "mechanical",
    "hot": "thermal",
    "image": "display",
    "input": "input",
    "interconnection": "communication",
    "joystick": "input",
    "led": "display",
    "leds": "display",
    "mains": "power",
    "motor": "mechanical",
    "motors": "mechanical",
    "network": "communication",
    "oil": "mechanical",
    "outlet": "power",
    "overheated": "thermal",
    "pendant": "input",
    "plc": "communication",
    "processor": "software",
    "program": "software",
    "rapid": "software",
    "screen": "display",
    "software": "software",
    "supervisory": "communication",
    "temperature": "thermal",
    "transformer": "power",
    "tripped": "power",
    "usb": "communication",
    "vibration": "mechanical",
    "voltage": "power",
}
_HIGH_SIGNAL_CONCEPTS = {
    "communication",
    "display",
    "input",
    "mechanical",
    "power",
    "software",
    "thermal",
}
_STOPWORDS = {
    "about",
    "affected",
    "available",
    "because",
    "cannot",
    "cause",
    "correctly",
    "describe",
    "device",
    "does",
    "during",
    "error",
    "from",
    "functions",
    "have",
    "into",
    "issue",
    "machine",
    "manual",
    "main",
    "malfunctioning",
    "module",
    "not",
    "open",
    "output",
    "power",
    "possible",
    "problem",
    "problems",
    "properly",
    "response",
    "related",
    "robot",
    "supply",
    "starts",
    "still",
    "system",
    "that",
    "their",
    "there",
    "they",
    "this",
    "unit",
    "with",
    "work",
    "works",
}


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) >= _MIN_TERM_LENGTH and token not in _STOPWORDS
    }


def _canonicalize_token(token: str) -> str:
    normalized = token.lower()
    if normalized in _TOKEN_ALIASES:
        return _TOKEN_ALIASES[normalized]
    if normalized.endswith("ies") and len(normalized) > 5:
        normalized = normalized[:-3] + "y"
    elif normalized.endswith("s") and len(normalized) > 4:
        normalized = normalized[:-1]
    return _TOKEN_ALIASES.get(normalized, normalized)


def _canonicalize_terms(terms: set[str]) -> set[str]:
    return {_canonicalize_token(term) for term in terms if term}


def _node_anchor_terms(node: dict[str, Any]) -> set[str]:
    if "component_id" in node:
        return _tokenize(str(node.get("name", "")))
    if "failure_mode_id" in node:
        anchor_text = str(node.get("material_context", "")).strip() or str(node.get("name", ""))
        return _tokenize(anchor_text)
    if "error_code_id" in node:
        return _tokenize(f"{node.get('code', '')} {node.get('name', '')}")
    return set()


def _node_support_terms(node: dict[str, Any]) -> set[str]:
    if "symptom_id" in node:
        return _tokenize(f"{node.get('name', '')} {node.get('description', '')}")
    return _node_anchor_terms(node)


def _alignment_cache(index: OntologyIndex) -> dict[str, Any]:
    cache = getattr(index, "_query_alignment_cache", None)
    if cache is not None:
        return cache

    query_anchor_terms_by_id: dict[str, set[str]] = {}
    canonical_anchor_terms_by_id: dict[str, set[str]] = {}
    support_terms_by_id: dict[str, set[str]] = {}
    canonical_support_terms_by_id: dict[str, set[str]] = {}
    anchor_term_frequency: dict[str, int] = {}

    for node_id, node in index.nodes_by_id.items():
        query_terms = _node_anchor_terms(node)
        support_terms = _node_support_terms(node)
        query_anchor_terms_by_id[node_id] = query_terms
        canonical_anchor_terms_by_id[node_id] = _canonicalize_terms(query_terms)
        support_terms_by_id[node_id] = support_terms
        canonical_support_terms_by_id[node_id] = _canonicalize_terms(support_terms)
        for term in query_terms:
            anchor_term_frequency[term] = anchor_term_frequency.get(term, 0) + 1

    cache = {
        "query_anchor_terms_by_id": query_anchor_terms_by_id,
        "canonical_anchor_terms_by_id": canonical_anchor_terms_by_id,
        "support_terms_by_id": support_terms_by_id,
        "canonical_support_terms_by_id": canonical_support_terms_by_id,
        "specific_anchor_terms": {
            term
            for term, count in anchor_term_frequency.items()
            if count <= QUERY_ALIGNMENT_MAX_TERM_FREQUENCY
        },
    }
    setattr(index, "_query_alignment_cache", cache)
    return cache


def extract_specific_query_terms(
    user_message: str,
    index: OntologyIndex,
) -> set[str]:
    cache = _alignment_cache(index)
    return _tokenize(user_message) & cache["specific_anchor_terms"]


def _group_direct_terms(group_paths: list[dict[str, Any]], index: OntologyIndex) -> set[str]:
    cache = _alignment_cache(index)
    query_anchor_terms_by_id: dict[str, set[str]] = cache["query_anchor_terms_by_id"]
    terms: set[str] = set()

    for path in group_paths:
        for key in _GROUP_NODE_ID_KEYS:
            node_id = path.get(key)
            if isinstance(node_id, str) and node_id:
                terms.update(query_anchor_terms_by_id.get(node_id, set()))

    return terms


def _group_direct_canonical_terms(group_paths: list[dict[str, Any]], index: OntologyIndex) -> set[str]:
    cache = _alignment_cache(index)
    canonical_anchor_terms_by_id: dict[str, set[str]] = cache["canonical_anchor_terms_by_id"]
    terms: set[str] = set()

    for path in group_paths:
        for key in _GROUP_NODE_ID_KEYS:
            node_id = path.get(key)
            if isinstance(node_id, str) and node_id:
                terms.update(canonical_anchor_terms_by_id.get(node_id, set()))

    return terms


def _group_support_terms(group_paths: list[dict[str, Any]], index: OntologyIndex) -> set[str]:
    cache = _alignment_cache(index)
    support_terms_by_id: dict[str, set[str]] = cache["support_terms_by_id"]
    terms: set[str] = set()

    for path in group_paths:
        for key in _GROUP_NODE_ID_KEYS:
            node_id = path.get(key)
            if isinstance(node_id, str) and node_id:
                terms.update(support_terms_by_id.get(node_id, set()))

    return terms


def _group_support_canonical_terms(group_paths: list[dict[str, Any]], index: OntologyIndex) -> set[str]:
    cache = _alignment_cache(index)
    canonical_support_terms_by_id: dict[str, set[str]] = cache["canonical_support_terms_by_id"]
    terms: set[str] = set()

    for path in group_paths:
        for key in _GROUP_NODE_ID_KEYS:
            node_id = path.get(key)
            if isinstance(node_id, str) and node_id:
                terms.update(canonical_support_terms_by_id.get(node_id, set()))

    return terms


def describe_group_alignment(
    group_paths: list[dict[str, Any]],
    index: OntologyIndex,
) -> dict[str, Any]:
    first_path = group_paths[0] if group_paths else {}
    symptom_ids = sorted({
        path.get("symptom_id", "").strip()
        for path in group_paths
        if path.get("symptom_id", "").strip()
    })
    component_names = sorted({
        path.get("component_name", "").strip()
        for path in group_paths
        if path.get("component_name", "").strip()
    })
    symptom_names = sorted({
        path.get("symptom_name", "").strip()
        for path in group_paths
        if path.get("symptom_name", "").strip()
    })
    direct_terms = _group_direct_terms(group_paths, index)
    direct_canonical_terms = _group_direct_canonical_terms(group_paths, index)
    support_terms = _group_support_terms(group_paths, index)
    support_canonical_terms = _group_support_canonical_terms(group_paths, index)

    return {
        "failure_mode_id": first_path.get("failure_mode_id", ""),
        "failure_mode_name": first_path.get("failure_mode_name", ""),
        "symptom_ids": symptom_ids,
        "component_names": component_names,
        "symptom_names": symptom_names,
        "symptom_descriptions": [
            str(index.nodes_by_id.get(symptom_id, {}).get("description", "")).strip()
            for symptom_id in symptom_ids
            if str(index.nodes_by_id.get(symptom_id, {}).get("description", "")).strip()
        ],
        "direct_terms": sorted(direct_terms),
        "direct_canonical_terms": sorted(direct_canonical_terms),
        "support_terms": sorted(support_terms),
        "support_canonical_terms": sorted(support_canonical_terms),
        "signal_concepts": sorted(direct_canonical_terms & _HIGH_SIGNAL_CONCEPTS),
    }


def _build_group_rerank_text(group_paths: list[dict[str, Any]], index: OntologyIndex) -> str:
    first_path = group_paths[0] if group_paths else {}
    failure_mode_id = first_path.get("failure_mode_id", "")
    fm_node = index.nodes_by_id.get(failure_mode_id, {})

    component_names = sorted({
        path.get("component_name", "").strip()
        for path in group_paths
        if path.get("component_name", "").strip()
    })
    symptom_names = sorted({
        path.get("symptom_name", "").strip()
        for path in group_paths
        if path.get("symptom_name", "").strip()
    })
    action_names = sorted({
        path.get("action_name", "").strip()
        for path in group_paths
        if path.get("action_name", "").strip()
    })

    parts = [
        str(first_path.get("failure_mode_name", "")),
        str(fm_node.get("description", "")),
        str(fm_node.get("material_context", "")),
    ]
    if component_names:
        parts.append("components: " + ", ".join(component_names))
    if symptom_names:
        parts.append("symptoms: " + ", ".join(symptom_names[:3]))
    if action_names:
        parts.append("actions: " + ", ".join(action_names[:3]))
    return ". ".join(part for part in parts if part).strip()


def _get_group_embedding(
    group_paths: list[dict[str, Any]],
    index: OntologyIndex,
) -> list[float] | None:
    if not group_paths:
        return None

    cache = getattr(index, "_group_rerank_embedding_cache", None)
    if cache is None:
        cache = {}
        setattr(index, "_group_rerank_embedding_cache", cache)

    failure_mode_id = group_paths[0].get("failure_mode_id", "")
    if not failure_mode_id:
        return None
    if failure_mode_id in cache:
        return cache[failure_mode_id]

    text = _build_group_rerank_text(group_paths, index)
    if not text:
        return None

    embeddings = get_text_embeddings([text])
    cache[failure_mode_id] = embeddings[0] if embeddings else None
    return cache[failure_mode_id]


def rerank_groups_for_query(
    ranked_groups: list[dict[str, Any]],
    user_message: str,
    query_embedding: list[float],
    index: OntologyIndex,
    top_symptoms: list[tuple[str, float]],
) -> list[dict[str, Any]]:
    if len(ranked_groups) <= 1:
        return ranked_groups

    query_terms = _tokenize(user_message)
    query_canonical_terms = _canonicalize_terms(query_terms)
    query_specific_terms = extract_specific_query_terms(user_message, index)
    score_map = {sid: score for sid, score in top_symptoms}

    rescored: list[tuple[float, int, dict[str, Any]]] = []
    for order, group in enumerate(ranked_groups):
        group_paths = group.get("paths", [])
        if not group_paths:
            rescored.append((0.0, order, group))
            continue

        base_score = max(score_map.get(path["symptom_id"], 0.0) for path in group_paths)
        direct_terms = _group_direct_terms(group_paths, index)
        direct_canonical_terms = _group_direct_canonical_terms(group_paths, index)
        support_canonical_terms = _group_support_canonical_terms(group_paths, index)

        direct_specific_overlap = len(query_specific_terms & direct_terms)
        direct_canonical_overlap = len(query_canonical_terms & direct_canonical_terms)
        support_canonical_overlap = len(query_canonical_terms & support_canonical_terms)

        query_signal_concepts = query_canonical_terms & _HIGH_SIGNAL_CONCEPTS
        group_signal_concepts = direct_canonical_terms & _HIGH_SIGNAL_CONCEPTS
        concept_mismatch_penalty = 0.0
        if query_signal_concepts and group_signal_concepts and not (query_signal_concepts & group_signal_concepts):
            concept_mismatch_penalty = 1.5
        extra_signal_penalty = 0.0
        if query_signal_concepts and group_signal_concepts:
            extra_signal_penalty = 0.25 * len(group_signal_concepts - query_signal_concepts)

        semantic_similarity = 0.0
        group_embedding = _get_group_embedding(group_paths, index)
        if group_embedding:
            semantic_similarity = cosine_similarity(query_embedding, group_embedding)

        score = (
            base_score * 4.0
            + semantic_similarity * 3.0
            + direct_specific_overlap * 2.5
            + direct_canonical_overlap * 2.5
            + support_canonical_overlap * 0.75
            - concept_mismatch_penalty
            - extra_signal_penalty
        )
        group["_query_rank_score"] = round(score, 6)
        rescored.append((score, order, group))

    rescored.sort(key=lambda item: (-item[0], item[1]))
    return [group for _, _, group in rescored]


def align_ranked_groups_to_query(
    ranked_groups: list[dict[str, Any]],
    user_message: str,
    index: OntologyIndex,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Prioritise groups that contain specific ontology terms explicitly mentioned by the user.

    Returns the reordered groups and, if no group matches the user's specific terms, the
    unmatched terms that should trigger a no-fit fallback.
    """
    query_terms = sorted(extract_specific_query_terms(user_message, index))
    if not query_terms:
        return ranked_groups, []

    query_term_set = set(query_terms)
    has_matching_group = False
    for group in ranked_groups:
        group_paths = group.get("paths", [])
        direct_terms = query_term_set & _group_direct_terms(group_paths, index)
        if direct_terms:
            has_matching_group = True
            break
        support_terms = query_term_set & _group_support_terms(group_paths, index)
        if support_terms:
            has_matching_group = True
            break

    if not has_matching_group:
        return [], query_terms

    return ranked_groups, []
