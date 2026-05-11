"""Build the log embeddings index for the IRC5 instance.

Reads `machine_logs.csv` and produces `log_embeddings.json` with:

- `occurrences`: {log_id: embedding}  -- one embedding per row's semantic_text
- `signatures`:  {event_signature_id: {
      "embedding": [...],
      "occurrence_count": N,
      "first_seen_at": iso,
      "last_seen_at": iso,
      "linked_failure_mode_id": str | "",
      "canonical_text": str,
   }}

For MVP both occurrences and signatures are embedded. Signatures use the
single longest semantic_text within their group as the canonical anchor (no
LLM rephrasing -- keeps the seed reproducible). Per the design doc, signature
embeddings are the primary retrieval target at scale; occurrence embeddings
help when the user query mentions a unique detail (date, WO id, axis, value)
that only exists in one row.

Usage:
    python -m kg_agents.scripts.embed_logs
    python -m kg_agents.scripts.embed_logs --force
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI

from kg_agents.config import DATA_DIR, OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL

INSTANCE_DIR = DATA_DIR / "instances" / "irc5-default-instance"
CSV_PATH = INSTANCE_DIR / "logs" / "machine_logs.csv"
OUTPUT_PATH = INSTANCE_DIR / "logs" / "log_embeddings.json"

# OpenAI accepts up to 2048 inputs per embeddings call. Stay well under.
EMBED_BATCH_SIZE = 256


def batch_embed(client: OpenAI, texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        chunk = texts[i : i + EMBED_BATCH_SIZE]
        resp = client.embeddings.create(model=OPENAI_EMBEDDING_MODEL, input=chunk)
        out.extend(d.embedding for d in resp.data)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--out", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 2
    if not args.csv.exists():
        print(f"ERROR: {args.csv} not found.", file=sys.stderr)
        return 2
    if args.out.exists() and not args.force:
        print(f"{args.out} already exists. Use --force to overwrite.")
        return 0

    with args.csv.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        rows = [{k: v for k, v in r.items() if k is not None} for r in reader]

    if not rows:
        print("No rows in CSV.")
        return 1

    client = OpenAI(api_key=OPENAI_API_KEY)

    # Per-occurrence embeddings
    print(f"Embedding {len(rows)} occurrences...")
    occ_texts = [r["semantic_text"] or r["title"] for r in rows]
    occ_embs = batch_embed(client, occ_texts)
    occurrences = {r["log_id"]: emb for r, emb in zip(rows, occ_embs)}

    # Build signatures: pick the longest semantic_text within each group as canonical
    by_sig: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_sig.setdefault(r["event_signature_id"], []).append(r)

    print(f"Building {len(by_sig)} signatures...")
    sig_canonical_texts: list[str] = []
    sig_meta: list[dict[str, Any]] = []
    for sig_id, group in by_sig.items():
        canonical = max(group, key=lambda r: len(r.get("semantic_text") or ""))
        canonical_text = canonical["semantic_text"] or canonical["title"]
        dates = sorted(r["occurred_at"] for r in group if r["occurred_at"])
        # within a signature, linked_failure_mode_id should be consistent;
        # if rows disagree (quality issues), pick the most common non-empty value.
        fm_counts: dict[str, int] = {}
        for r in group:
            v = r.get("linked_failure_mode_id", "")
            if v:
                fm_counts[v] = fm_counts.get(v, 0) + 1
        linked_fm = max(fm_counts.items(), key=lambda kv: kv[1])[0] if fm_counts else ""

        sig_canonical_texts.append(canonical_text)
        sig_meta.append({
            "event_signature_id": sig_id,
            "occurrence_count": len(group),
            "first_seen_at": dates[0] if dates else "",
            "last_seen_at": dates[-1] if dates else "",
            "linked_failure_mode_id": linked_fm,
            "canonical_text": canonical_text,
        })

    sig_embs = batch_embed(client, sig_canonical_texts)
    signatures = {
        meta["event_signature_id"]: {**meta, "embedding": emb}
        for meta, emb in zip(sig_meta, sig_embs)
    }

    payload = {
        "model": OPENAI_EMBEDDING_MODEL,
        "instance_id": "irc5-default-instance",
        "occurrences": occurrences,
        "signatures": signatures,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        json.dump(payload, f)

    print(f"Wrote {len(occurrences)} occurrence + {len(signatures)} signature embeddings to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
