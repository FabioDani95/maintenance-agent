"""Hybrid retrieval over instance logs.

Combines:
  1. Dense search over per-occurrence embeddings (text-embedding-3-large).
  2. Sparse TF-IDF search over title + body + action_taken + semantic_text +
     codes/tags (catches exact tokens like work order ids and error codes that
     dense embeddings tend to wash out).
  3. Reciprocal Rank Fusion to merge both candidate sets.
  4. Optional LLM rerank of the top-N fused candidates.
  5. Aggregation by event_signature_id for the final response.

Filters are applied to the candidate pool so retrieval never surfaces rows the
caller wanted excluded.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import numpy as np
from openai import OpenAI
from sklearn.feature_extraction.text import TfidfVectorizer

from kg_agents.config import OPENAI_API_KEY, OPENAI_CHAT_MODEL, OPENAI_EMBEDDING_MODEL

from .log_loader import LogStore, load_log_store
from .similarity import cosine_similarity

logger = logging.getLogger(__name__)


# Tunables. Held here rather than in config.py because they're log-specific
# and may need to be tweaked independently of KG retrieval thresholds.
DENSE_TOP_K = 25
SPARSE_TOP_K = 25
RRF_K = 60
RERANK_TOP_N = 12
DEFAULT_RESULT_LIMIT = 5
_SIGNATURE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_:-]*$")
_SOLUTION_QUERY_RE = re.compile(
    r"\b("
    r"solution|fix|fixed|resolve|resolved|repair|repaired|"
    r"what\s+did\s+we\s+do|what\s+was\s+done|last\s+time|previous\s+fix"
    r")\b",
    re.IGNORECASE,
)
_RECENCY_QUERY_RE = re.compile(r"\b(last\s+time|latest|most\s+recent)\b", re.IGNORECASE)


@dataclass
class SparseIndex:
    vectorizer: TfidfVectorizer
    matrix: Any  # scipy sparse
    log_ids: list[str] = field(default_factory=list)


_SPARSE_CACHE: dict[str, SparseIndex] = {}
_OPENAI_CLIENT: OpenAI | None = None


def is_presentable_signature_id(signature_id: str | None) -> bool:
    """Return whether a log signature is suitable for summaries/UI labels."""
    if not signature_id or signature_id == "_unsignatured":
        return False
    return bool(_SIGNATURE_ID_PATTERN.fullmatch(signature_id))


def _is_solution_query(query: str) -> bool:
    return bool(_SOLUTION_QUERY_RE.search(query or ""))


def _is_recency_solution_query(query: str) -> bool:
    return bool(_RECENCY_QUERY_RE.search(query or ""))


def _outcome_score(row: dict[str, Any]) -> int:
    outcome = str(row.get("outcome") or "").lower()
    status = str(row.get("status") or "").lower()
    score = 0
    if outcome == "resolved":
        score += 50
    elif "partial" in outcome:
        score += 25
    elif "monitor" in outcome:
        score += 10
    if status in {"closed", "completed", "resolved"}:
        score += 20
    elif status == "open":
        score -= 10
    return score


def _solution_row_score(row: dict[str, Any], query: str) -> tuple:
    action = str(row.get("action_taken") or "").strip()
    action_score = min(len(action), 240) if action else -100
    outcome_score = _outcome_score(row)
    occurred_at = str(row.get("occurred_at") or "")
    if _is_recency_solution_query(query):
        return (action_score > 0, occurred_at, outcome_score, action_score)
    return (action_score > 0, outcome_score, action_score, occurred_at)


def _select_top_match_log(
    query: str,
    anchor_row: dict[str, Any],
    all_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if not _is_solution_query(query) or not all_rows:
        return anchor_row
    return max(all_rows, key=lambda row: _solution_row_score(row, query))


def _get_client() -> OpenAI:
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        _OPENAI_CLIENT = OpenAI(api_key=OPENAI_API_KEY)
    return _OPENAI_CLIENT


def evict_search_cache(instance_id: str | None = None) -> None:
    """Drop cached sparse index. Pair with `evict_log_cache` to force reload."""
    if instance_id is None:
        _SPARSE_CACHE.clear()
        return
    _SPARSE_CACHE.pop(instance_id, None)


def _row_text_for_sparse(row: dict[str, Any]) -> str:
    """Concatenate fields where exact-token matches matter."""
    parts: list[str] = []
    for key in (
        "title", "body", "action_taken", "semantic_text",
        "event_name", "component_name_raw",
        "error_code", "alarm_code", "signal_name",
        "work_order_id", "equipment_tag", "linked_failure_mode_id",
    ):
        v = row.get(key)
        if v:
            parts.append(str(v))
    return " ".join(parts)


def _build_sparse_index(store: LogStore) -> SparseIndex:
    vectorizer = TfidfVectorizer(
        lowercase=True,
        analyzer="word",
        ngram_range=(1, 2),
        min_df=1,
        sublinear_tf=True,
    )
    docs = [_row_text_for_sparse(r) for r in store.rows]
    log_ids = [r["log_id"] for r in store.rows]
    matrix = vectorizer.fit_transform(docs) if docs else None
    return SparseIndex(vectorizer=vectorizer, matrix=matrix, log_ids=log_ids)


def _get_sparse_index(store: LogStore) -> SparseIndex:
    if store.instance_id in _SPARSE_CACHE:
        return _SPARSE_CACHE[store.instance_id]
    idx = _build_sparse_index(store)
    _SPARSE_CACHE[store.instance_id] = idx
    return idx


def _passes_filters(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    if not filters:
        return True

    df = filters.get("date_from")
    if df and (row.get("occurred_at") or "") < df:
        return False
    dt = filters.get("date_to")
    if dt and (row.get("occurred_at") or "") > dt:
        return False

    for key in ("component_id", "linked_failure_mode_id", "maintenance_type",
                "event_category", "status", "event_signature_id"):
        if key in filters and filters[key] is not None:
            if row.get(key) != filters[key]:
                return False

    sm = filters.get("severity_min")
    if sm is not None:
        rs = row.get("severity_number") or 0
        if rs < int(sm):
            return False

    return True


def _embed_query(query: str) -> list[float]:
    resp = _get_client().embeddings.create(
        model=OPENAI_EMBEDDING_MODEL, input=query,
    )
    return resp.data[0].embedding


def _dense_scores(
    query_emb: list[float],
    store: LogStore,
    allowed_ids: set[str],
) -> list[tuple[str, float]]:
    if not store.occurrence_embeddings:
        return []
    out: list[tuple[str, float]] = []
    qa = np.asarray(query_emb, dtype=np.float32)
    qn = float(np.linalg.norm(qa))
    if qn == 0:
        return []
    for log_id, emb in store.occurrence_embeddings.items():
        if log_id not in allowed_ids:
            continue
        ea = np.asarray(emb, dtype=np.float32)
        en = float(np.linalg.norm(ea))
        score = float(np.dot(qa, ea) / (qn * en)) if en > 0 else 0.0
        out.append((log_id, score))
    out.sort(key=lambda x: x[1], reverse=True)
    return out[:DENSE_TOP_K]


def _sparse_scores(
    query: str,
    store: LogStore,
    allowed_ids: set[str],
) -> list[tuple[str, float]]:
    idx = _get_sparse_index(store)
    if idx.matrix is None or idx.matrix.shape[0] == 0:
        return []
    qv = idx.vectorizer.transform([query])
    sims = (idx.matrix @ qv.T).toarray().reshape(-1)
    pairs = [
        (idx.log_ids[i], float(sims[i]))
        for i in range(len(idx.log_ids))
        if idx.log_ids[i] in allowed_ids and sims[i] > 0
    ]
    pairs.sort(key=lambda x: x[1], reverse=True)
    return pairs[:SPARSE_TOP_K]


def _rrf_fuse(
    *ranked_lists: list[tuple[str, float]],
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: 1 / (k + rank)."""
    fused: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, (item_id, _score) in enumerate(ranked):
            fused[item_id] = fused.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(fused.items(), key=lambda x: x[1], reverse=True)


def _llm_rerank(
    query: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ask the LLM to score each candidate's relevance to the query.

    Returns candidates with `score` (0..1) and `rationale` keys, sorted by
    score desc. The query intent is decided by the caller; here we just judge
    relevance to the literal query.
    """
    if not candidates:
        return []
    payload = {
        "query": query,
        "candidates": [
            {
                "log_id": c["log_id"],
                "title": c.get("title", ""),
                "body": c.get("body", ""),
                "action_taken": c.get("action_taken", ""),
                "occurred_at": c.get("occurred_at", ""),
                "severity_text": c.get("severity_text", ""),
                "outcome": c.get("outcome", ""),
                "status": c.get("status", ""),
                "event_name": c.get("event_name", ""),
                "event_category": c.get("event_category", ""),
                "event_signature_id": c.get("event_signature_id", ""),
                "linked_failure_mode_id": c.get("linked_failure_mode_id", ""),
                "component_name_raw": c.get("component_name_raw", ""),
                "work_order_id": c.get("work_order_id", ""),
                "error_code": c.get("error_code", ""),
                "alarm_code": c.get("alarm_code", ""),
                "signal_name": c.get("signal_name", ""),
                "observed_value": c.get("observed_value"),
                "observed_unit": c.get("observed_unit", ""),
                "threshold_value": c.get("threshold_value"),
                "equipment_tag": c.get("equipment_tag", ""),
            }
            for c in candidates
        ],
    }
    system = (
        "You are a relevance-scoring component for an industrial maintenance "
        "log retrieval system. Given a user query and candidate log entries, "
        "score each candidate's relevance to the query as a number between 0 "
        "and 1. Higher scores mean the candidate directly answers, exemplifies, "
        "or strongly relates to the query. Be strict: a vaguely related "
        "maintenance entry should score below 0.4, a directly relevant entry "
        "above 0.7. Provide a one-sentence rationale per candidate.\n\n"
        "Return a JSON object with key 'rankings' whose value is an array of "
        "objects {log_id, score, rationale}. Include EVERY candidate exactly "
        "once. Preserve no particular order."
    )
    resp = _get_client().chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    parsed = json.loads(content)
    rankings = parsed.get("rankings") or parsed.get("results") or []

    by_id = {c["log_id"]: c for c in candidates}
    out: list[dict[str, Any]] = []
    for r in rankings:
        log_id = r.get("log_id")
        if log_id not in by_id:
            continue
        cand = dict(by_id[log_id])
        try:
            cand["rerank_score"] = float(r.get("score", 0.0))
        except (TypeError, ValueError):
            cand["rerank_score"] = 0.0
        cand["rerank_rationale"] = str(r.get("rationale", ""))
        out.append(cand)

    # Append any candidate the LLM forgot, with 0 score (defensive)
    seen = {c["log_id"] for c in out}
    for c in candidates:
        if c["log_id"] not in seen:
            cand = dict(c)
            cand["rerank_score"] = 0.0
            cand["rerank_rationale"] = "(missing from LLM response)"
            out.append(cand)

    out.sort(key=lambda x: x["rerank_score"], reverse=True)
    return out


def _aggregate_by_signature(
    ranked_rows: list[dict[str, Any]],
    store: LogStore,
    score_key: str,
    limit: int,
    query: str = "",
) -> list[dict[str, Any]]:
    """Collapse occurrence-level results into signature-level matches.

    Each signature's score is the max occurrence score within it; the latest
    occurrence (by occurred_at) becomes the anchor.
    """
    by_sig: dict[str, dict[str, Any]] = {}
    for row in ranked_rows:
        sig = row.get("event_signature_id") or "_unsignatured"
        if not is_presentable_signature_id(sig):
            continue
        score = row.get(score_key, 0.0)
        existing = by_sig.get(sig)
        if existing is None:
            by_sig[sig] = {
                "_top_score": score,
                "_anchor_row": row,
                "_matched_rows": [row],
            }
            continue
        existing["_matched_rows"].append(row)
        if score > existing["_top_score"]:
            existing["_top_score"] = score
            existing["_anchor_row"] = row

    matches: list[dict[str, Any]] = []
    for sig, data in by_sig.items():
        all_rows = store.rows_by_signature.get(sig, [])
        matched_rows = data.get("_matched_rows") or []
        most_recent = all_rows[0] if all_rows else data["_anchor_row"]
        top_match = _select_top_match_log(query, data["_anchor_row"], all_rows)
        meta = store.signature_meta.get(sig, {})
        matches.append({
            "event_signature_id": sig,
            "score": round(float(data["_top_score"]), 4),
            "matched_occurrence_count": len(matched_rows),
            "occurrence_count": len(all_rows),
            "first_seen_at": meta.get("first_seen_at"),
            "last_seen_at": meta.get("last_seen_at"),
            "linked_failure_mode_id": (
                top_match.get("linked_failure_mode_id")
                or most_recent.get("linked_failure_mode_id")
                or meta.get("linked_failure_mode_id")
                or ""
            ),
            "linked_symptom_id": top_match.get("linked_symptom_id") or "",
            "top_match_log": top_match,
            "most_recent_log": most_recent,
            "matched_log_ids": [r["log_id"] for r in matched_rows if r.get("log_id")],
            "all_log_ids": [r["log_id"] for r in all_rows],
            "rerank_rationale": top_match.get("rerank_rationale"),
        })

    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches[:limit]


def search_logs(
    query: str,
    instance_id: str,
    filters: dict[str, Any] | None = None,
    limit: int = DEFAULT_RESULT_LIMIT,
    use_llm_rerank: bool = True,
) -> dict[str, Any]:
    """Run hybrid search and return signature-level matches."""
    started = perf_counter()
    last = started
    timings: dict[str, float] = {}

    def mark(stage: str) -> None:
        nonlocal last
        now = perf_counter()
        timings[f"{stage}_s"] = round(now - last, 3)
        last = now

    def diagnostics(extra: dict[str, Any]) -> dict[str, Any]:
        return {
            **extra,
            "timings": {
                **timings,
                "total_s": round(perf_counter() - started, 3),
            },
        }

    store = load_log_store(instance_id)
    mark("load_store")
    if store is None or store.is_empty:
        return {
            "query": query,
            "instance_id": instance_id,
            "match_count": 0,
            "matches": [],
            "diagnostics": diagnostics({"reason": "no_logs_for_instance"}),
        }

    filters = filters or {}
    allowed_ids = {
        r["log_id"] for r in store.rows
        if _passes_filters(r, filters)
    }
    mark("filters")
    if not allowed_ids:
        return {
            "query": query,
            "instance_id": instance_id,
            "match_count": 0,
            "matches": [],
            "diagnostics": diagnostics({"reason": "filters_excluded_all_rows"}),
        }

    query_emb = _embed_query(query) if store.occurrence_embeddings else []
    mark("embed_query")
    dense = _dense_scores(query_emb, store, allowed_ids) if query_emb else []
    mark("dense_scores")
    sparse = _sparse_scores(query, store, allowed_ids)
    mark("sparse_scores")

    if not dense and not sparse:
        return {
            "query": query,
            "instance_id": instance_id,
            "match_count": 0,
            "matches": [],
            "diagnostics": diagnostics({"reason": "no_candidates_from_dense_or_sparse"}),
        }

    fused = _rrf_fuse(dense, sparse)
    top_fused_ids = [log_id for log_id, _ in fused[:RERANK_TOP_N]]

    candidate_rows: list[dict[str, Any]] = []
    fused_score_by_id = {log_id: score for log_id, score in fused}
    for log_id in top_fused_ids:
        row = store.rows_by_id.get(log_id)
        if row is None:
            continue
        enriched = dict(row)
        enriched["fused_score"] = fused_score_by_id.get(log_id, 0.0)
        candidate_rows.append(enriched)
    mark("fuse_candidates")

    if use_llm_rerank and candidate_rows:
        try:
            reranked = _llm_rerank(query, candidate_rows)
            score_key = "rerank_score"
        except Exception as e:
            logger.warning("LLM rerank failed (%s); falling back to RRF.", e)
            reranked = candidate_rows
            score_key = "fused_score"
    else:
        reranked = candidate_rows
        score_key = "fused_score"
    mark("llm_rerank" if use_llm_rerank and candidate_rows else "skip_rerank")

    matches = _aggregate_by_signature(reranked, store, score_key, limit, query=query)
    mark("aggregate")

    return {
        "query": query,
        "instance_id": instance_id,
        "match_count": len(matches),
        "matches": matches,
        "diagnostics": diagnostics({
            "dense_candidates": len(dense),
            "sparse_candidates": len(sparse),
            "fused_candidates": len(fused),
            "rerank_used": bool(use_llm_rerank and candidate_rows),
            "candidate_pool_size": len(allowed_ids),
        }),
    }


def summarize_logs(instance_id: str) -> dict[str, Any]:
    """Aggregate analytics over all logs for an instance.

    Returns top signatures, top components, severity distribution, monthly
    counts, open events, and downtime by component.
    """
    store = load_log_store(instance_id)
    if store is None or store.is_empty:
        return {"instance_id": instance_id, "row_count": 0}

    rows = store.rows
    from collections import Counter, defaultdict

    sig_counts = Counter(
        sig for r in rows
        if is_presentable_signature_id(sig := r.get("event_signature_id"))
    )
    comp_counts = Counter(
        r.get("component_id") or "(unspecified)" for r in rows
    )
    sev_dist = Counter(r.get("severity_text") or "(none)" for r in rows)
    open_count = sum(
        1 for r in rows
        if (r.get("status") or "").lower() in {"open", "in_progress", "released"}
    )

    months: dict[str, int] = defaultdict(int)
    for r in rows:
        ts = r.get("occurred_at") or ""
        if len(ts) >= 7:
            months[ts[:7]] += 1

    downtime_by_comp: dict[str, int] = defaultdict(int)
    for r in rows:
        comp = r.get("component_id") or "(unspecified)"
        dt = r.get("downtime_min") or 0
        if isinstance(dt, (int, float)):
            downtime_by_comp[comp] += int(dt)

    def top_with_meta(sig_id: str) -> dict[str, Any]:
        meta = store.signature_meta.get(sig_id, {})
        return {
            "event_signature_id": sig_id,
            "occurrence_count": sig_counts[sig_id],
            "linked_failure_mode_id": meta.get("linked_failure_mode_id") or "",
            "last_seen_at": meta.get("last_seen_at"),
        }

    return {
        "instance_id": instance_id,
        "row_count": len(rows),
        "top_event_signatures": [
            top_with_meta(sig) for sig, _ in sig_counts.most_common(10)
        ],
        "top_components": [
            {"component_id": c, "count": n} for c, n in comp_counts.most_common(10)
        ],
        "severity_distribution": dict(sev_dist),
        "events_by_month": dict(sorted(months.items())),
        "open_events": open_count,
        "downtime_by_component_min": dict(
            sorted(downtime_by_comp.items(), key=lambda kv: kv[1], reverse=True)
        ),
    }
