"""Verify and refine log -> ontology links via embedding similarity.

For each row in machine_logs.csv, computes the embedding similarity between
`semantic_text` and every FailureMode in the IRC5 ontology, then:

- If the LLM-seeded `linked_failure_mode_id` is the top match (or above
  threshold), keeps it. Otherwise marks `quality_flags` with
  `unmapped_failure_mode` if no good candidate exists, or appends
  `link_low_confidence` if the seeded link disagrees with the embedding match
  but a stronger candidate exists.
- Populates `linked_symptom_id` with the best Symptom match above threshold.

This simulates the future ingest pipeline where logs arrive without curated
links and a verification step reconciles them with the knowledge graph.

Usage:
    python -m kg_agents.scripts.verify_log_links
    python -m kg_agents.scripts.verify_log_links --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI

from kg_agents.config import (
    DATA_DIR,
    FAILURE_MODE_SIMILARITY_THRESHOLD,
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL,
    SIMILARITY_THRESHOLD,
)

INSTANCE_DIR = DATA_DIR / "instances" / "irc5-default-instance"
CSV_PATH = INSTANCE_DIR / "logs" / "machine_logs.csv"
ONTOLOGY_PATH = INSTANCE_DIR / "ontology.json"

# A successfully seeded LLM link is kept if it scores within this delta of the
# top embedding match — embeddings are noisy enough that demanding strict #1
# would override well-curated LLM links for trivially-different similarity.
KEEP_SEEDED_LINK_DELTA = 0.04


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def fm_text(fm: dict[str, Any]) -> str:
    parts = [
        fm.get("name", ""),
        fm.get("description", ""),
        fm.get("material_context", ""),
    ]
    return ". ".join(p for p in parts if p)


def sym_text(s: dict[str, Any]) -> str:
    parts = [s.get("name", ""), s.get("description", "")]
    return ". ".join(p for p in parts if p)


def batch_embed(client: OpenAI, texts: list[str]) -> list[list[float]]:
    """Embed many texts in one call (OpenAI accepts batched input)."""
    if not texts:
        return []
    resp = client.embeddings.create(model=OPENAI_EMBEDDING_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def load_ontology_targets() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with ONTOLOGY_PATH.open("r", encoding="utf-8") as f:
        ont = json.load(f)
    return ont["nodes"].get("FailureMode", []), ont["nodes"].get("Symptom", [])


def append_quality_flag(existing: str, new_flag: str) -> str:
    flags = [f for f in existing.split(",") if f] if existing else []
    if new_flag not in flags:
        flags.append(new_flag)
    return ",".join(flags)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 2
    if not args.csv.exists():
        print(f"ERROR: {args.csv} not found. Run generate_irc5_logs first.", file=sys.stderr)
        return 2

    failure_modes, symptoms = load_ontology_targets()
    print(f"Loaded {len(failure_modes)} failure modes and {len(symptoms)} symptoms.")

    client = OpenAI(api_key=OPENAI_API_KEY)

    print("Embedding ontology targets...")
    fm_texts = [fm_text(fm) for fm in failure_modes]
    sym_texts = [sym_text(s) for s in symptoms]
    fm_embs = batch_embed(client, fm_texts)
    sym_embs = batch_embed(client, sym_texts)
    fm_ids = [fm["failure_mode_id"] for fm in failure_modes]
    sym_ids = [s["symptom_id"] for s in symptoms]

    with args.csv.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    print(f"Embedding {len(rows)} log semantic_text entries...")
    log_embs = batch_embed(client, [r["semantic_text"] or r["title"] for r in rows])

    stats = {
        "kept_seeded": 0,
        "replaced_seeded": 0,
        "filled_missing": 0,
        "still_unmapped": 0,
        "symptom_linked": 0,
    }

    for row, emb in zip(rows, log_embs):
        # Failure mode resolution
        fm_scores = sorted(
            ((fid, cosine(emb, fe)) for fid, fe in zip(fm_ids, fm_embs)),
            key=lambda x: x[1],
            reverse=True,
        )
        top_id, top_score = fm_scores[0]
        seeded = row.get("linked_failure_mode_id", "").strip()

        if seeded:
            seeded_score = next((s for fid, s in fm_scores if fid == seeded), 0.0)
            if seeded == top_id or (top_score - seeded_score) <= KEEP_SEEDED_LINK_DELTA:
                stats["kept_seeded"] += 1
            else:
                # Embedding strongly disagrees with the LLM-seeded link.
                # Trust the embedding only if it's confidently above threshold.
                if top_score >= FAILURE_MODE_SIMILARITY_THRESHOLD:
                    row["linked_failure_mode_id"] = top_id
                    row["quality_flags"] = append_quality_flag(
                        row.get("quality_flags", ""), "link_replaced_by_embedding"
                    )
                    stats["replaced_seeded"] += 1
                else:
                    stats["kept_seeded"] += 1
        else:
            # No seeded link. Try to fill if confident.
            if top_score >= FAILURE_MODE_SIMILARITY_THRESHOLD:
                # Only fill if the row was not deliberately left unlinked
                # (operator_note category and unmapped_failure_mode flag).
                if (
                    row.get("event_category") != "operator_note"
                    and "unmapped_failure_mode" not in row.get("quality_flags", "")
                ):
                    row["linked_failure_mode_id"] = top_id
                    row["quality_flags"] = append_quality_flag(
                        row.get("quality_flags", ""), "link_inferred_by_embedding"
                    )
                    stats["filled_missing"] += 1
                else:
                    stats["still_unmapped"] += 1
            else:
                stats["still_unmapped"] += 1

        # Symptom resolution (best match above threshold)
        sym_scores = sorted(
            ((sid, cosine(emb, se)) for sid, se in zip(sym_ids, sym_embs)),
            key=lambda x: x[1],
            reverse=True,
        )
        if sym_scores:
            best_sid, best_score = sym_scores[0]
            if best_score >= SIMILARITY_THRESHOLD:
                row["linked_symptom_id"] = best_sid
                stats["symptom_linked"] += 1

    print("\nLink resolution summary:")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    if args.dry_run:
        print("\n--dry-run: not writing back to CSV.")
        return 0

    with args.csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print(f"\nUpdated {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
