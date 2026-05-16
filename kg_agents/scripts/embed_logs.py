"""Build the log embeddings index for a kg_agents instance.

Reads `instances/<instance_id>/logs/machine_logs.csv` and produces
`log_embeddings.json` with:

- `occurrences`: {log_id: embedding}  -- one embedding per row's descriptive text
- `signatures`:  {event_signature_id: {
      "embedding": [...],
      "occurrence_count": N,
      "first_seen_at": iso,
      "last_seen_at": iso,
      "linked_failure_mode_id": str | "",
      "canonical_text": str,
   }}

Per the design doc, signature embeddings are the primary retrieval target at
scale; occurrence embeddings help when the user query mentions a unique detail
(date, WO id, axis, value) that only exists in one row.

Usage:
    python -m kg_agents.scripts.embed_logs
    python -m kg_agents.scripts.embed_logs --instance cnc-haas-vf2-instance
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

DEFAULT_INSTANCE = "irc5-default-instance"

# OpenAI accepts up to 2048 inputs per embeddings call. Stay well under.
EMBED_BATCH_SIZE = 256

# Reject `semantic_text` shorter than this OR purely numeric — those are
# misaligned-column artifacts (values like "0", "30", "45") that, if embedded,
# collapse unrelated rows into the same vector cluster.
_MIN_SEMANTIC_TEXT_LEN = 12


def _is_usable_semantic_text(value: Any) -> bool:
    if not value:
        return False
    s = str(value).strip()
    if len(s) < _MIN_SEMANTIC_TEXT_LEN:
        return False
    # Reject "12345", "30.5", "+42", etc.
    if s.replace(".", "", 1).replace("-", "", 1).replace("+", "", 1).isdigit():
        return False
    return True


def _composed_text(row: dict[str, Any]) -> str:
    """Compose a descriptive text from the row when semantic_text is unusable.

    Concatenates the user-facing fields most likely to carry semantic content.
    """
    parts: list[str] = []
    for key in (
        "title", "event_name", "component_name_raw", "body", "action_taken",
        "error_code", "alarm_code", "signal_name",
    ):
        v = row.get(key)
        if v:
            parts.append(str(v).strip())
    return " · ".join(p for p in parts if p)


def _occurrence_text(row: dict[str, Any]) -> str:
    if _is_usable_semantic_text(row.get("semantic_text")):
        return str(row["semantic_text"]).strip()
    return _composed_text(row) or str(row.get("title") or "")


def _canonical_text_for_group(group: list[dict[str, Any]]) -> str:
    """Pick the most informative text within a signature group.

    Prefers the longest usable `semantic_text`; falls back to the row with the
    longest composed text. Avoids picking junk numeric `semantic_text`.
    """
    usable = [r for r in group if _is_usable_semantic_text(r.get("semantic_text"))]
    if usable:
        best = max(usable, key=lambda r: len(str(r["semantic_text"])))
        return str(best["semantic_text"]).strip()
    best = max(group, key=lambda r: len(_composed_text(r)))
    return _composed_text(best) or str(best.get("title") or "")


def batch_embed(client: OpenAI, texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        chunk = texts[i : i + EMBED_BATCH_SIZE]
        resp = client.embeddings.create(model=OPENAI_EMBEDDING_MODEL, input=chunk)
        out.extend(d.embedding for d in resp.data)
    return out


def _paths_for_instance(instance_id: str) -> tuple[Path, Path]:
    base = DATA_DIR / "instances" / instance_id / "logs"
    return base / "machine_logs.csv", base / "log_embeddings.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", default=DEFAULT_INSTANCE,
                        help="Instance id under data/instances/")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--csv", type=Path, default=None,
                        help="Override CSV path (defaults to instance's machine_logs.csv)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Override output path (defaults to instance's log_embeddings.json)")
    args = parser.parse_args()

    default_csv, default_out = _paths_for_instance(args.instance)
    csv_path = args.csv or default_csv
    out_path = args.out or default_out

    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 2
    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found.", file=sys.stderr)
        return 2
    if out_path.exists() and not args.force:
        print(f"{out_path} already exists. Use --force to overwrite.")
        return 0

    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        rows = [{k: v for k, v in r.items() if k is not None} for r in reader]

    if not rows:
        print("No rows in CSV.")
        return 1

    client = OpenAI(api_key=OPENAI_API_KEY)

    # Per-occurrence embeddings
    occ_texts = [_occurrence_text(r) for r in rows]
    rejected_semantic = sum(
        1 for r in rows if not _is_usable_semantic_text(r.get("semantic_text"))
    )
    print(
        f"Embedding {len(rows)} occurrences "
        f"(fallback-to-composed-text on {rejected_semantic} rows where semantic_text was empty/numeric/too-short)..."
    )
    occ_embs = batch_embed(client, occ_texts)
    occurrences = {r["log_id"]: emb for r, emb in zip(rows, occ_embs)}

    # Build signatures: pick the longest usable text within each group
    by_sig: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_sig.setdefault(r["event_signature_id"], []).append(r)

    print(f"Building {len(by_sig)} signatures...")
    sig_canonical_texts: list[str] = []
    sig_meta: list[dict[str, Any]] = []
    for sig_id, group in by_sig.items():
        canonical_text = _canonical_text_for_group(group)
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
        "instance_id": args.instance,
        "occurrences": occurrences,
        "signatures": signatures,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f)

    print(
        f"Wrote {len(occurrences)} occurrence + {len(signatures)} signature "
        f"embeddings for instance {args.instance} to {out_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
