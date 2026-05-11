"""Normalize machine_logs.csv before embedding.

Three passes applied in order:
  1. severity_text normalization — deterministic aliases (WARN→WARNING, ERR→ERROR, …)
  2. component_id resolution — LLM fuzzy-maps blank/unknown component_name_raw
     to a canonical component_id from the registry built from existing rows
  3. semantic_text reconstruction — LLM synthesises a clean English semantic_text
     from title+body+codes when the field is empty or quality_flags includes
     "no_semantic" or "low_quality_semantic"

Each LLM pass is batched (LLM_BATCH rows per call).  Rows are tagged with
"comp_normalized" or "semantic_reconstructed" in quality_flags so you can
audit which rows were auto-fixed.

Writes a .bak backup of the original CSV, then overwrites it in place so
embed_logs.py and log_loader.py need no changes.

Usage:
    python -m kg_agents.scripts.normalize_logs
    python -m kg_agents.scripts.normalize_logs --csv /path/to/other.csv
    python -m kg_agents.scripts.normalize_logs --no-backup
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import sys
from pathlib import Path

from openai import OpenAI

from kg_agents.config import DATA_DIR, OPENAI_API_KEY, OPENAI_CHAT_MODEL

INSTANCE_DIR = DATA_DIR / "instances" / "irc5-default-instance"
CSV_PATH = INSTANCE_DIR / "logs" / "machine_logs.csv"

LLM_BATCH = 20  # rows per LLM call

logger = logging.getLogger(__name__)


# ── Pass 1: severity_text normalization ───────────────────────────────────────

_SEV_ALIASES: dict[str, str] = {
    "WARN": "WARNING",
    "ERR": "ERROR",
    "CRIT": "CRITICAL",
    "FATAL": "CRITICAL",
    "DBG": "DEBUG",
    "TRACE": "DEBUG",
}


def _fix_severity(row: dict[str, str]) -> bool:
    """Normalize severity_text alias in place. Returns True if changed."""
    raw = (row.get("severity_text") or "").strip()
    canonical = _SEV_ALIASES.get(raw.upper())
    if canonical:
        row["severity_text"] = canonical
        return True
    return False


# ── Pass 2: component_id resolution ──────────────────────────────────────────

def _build_registry(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    """Build component_id → [known raw name variants] from rows that already have a valid ID."""
    reg: dict[str, list[str]] = {}
    for row in rows:
        cid = (row.get("component_id") or "").strip()
        raw = (row.get("component_name_raw") or "").strip()
        if cid and cid not in ("", "unknown") and raw:
            names = reg.setdefault(cid, [])
            if raw not in names:
                names.append(raw)
    return reg


def _needs_comp_resolution(row: dict[str, str]) -> bool:
    cid = (row.get("component_id") or "").strip()
    return not cid or cid == "unknown"


def _resolve_comp_batch(
    client: OpenAI,
    batch: list[dict[str, str]],
    registry: dict[str, list[str]],
) -> dict[str, str]:
    """Return {log_id: canonical_component_id} for a batch of rows."""
    reg_summary = {cid: names[:3] for cid, names in registry.items()}
    candidates = [
        {
            "log_id": r["log_id"],
            "component_name_raw": r.get("component_name_raw") or "",
            "title": r.get("title") or "",
        }
        for r in batch
    ]
    prompt = (
        "You are a component normalizer for industrial robot maintenance logs.\n"
        "Map each candidate to the closest canonical component_id from the registry.\n"
        "Return 'unknown' only when nothing is a reasonable match.\n"
        "Candidates may be in any language; match by meaning, not exact spelling.\n\n"
        f"Registry (component_id → sample raw names):\n"
        f"{json.dumps(reg_summary, ensure_ascii=False)}\n\n"
        f"Candidates:\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
        'Return JSON: {"results": [{"log_id": "...", "component_id": "..."}]}'
    )
    resp = client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    return {r["log_id"]: r["component_id"] for r in (data.get("results") or [])}


# ── Pass 3: semantic_text reconstruction ─────────────────────────────────────

def _needs_semantic_reconstruction(row: dict[str, str]) -> bool:
    sem = (row.get("semantic_text") or "").strip()
    flags = {f.strip() for f in (row.get("quality_flags") or "").split(",") if f.strip()}
    return not sem or "no_semantic" in flags or "low_quality_semantic" in flags


def _reconstruct_sem_batch(
    client: OpenAI,
    batch: list[dict[str, str]],
) -> dict[str, str]:
    """Return {log_id: semantic_text} reconstructed from raw fields."""
    candidates = [
        {
            "log_id": r["log_id"],
            "title": r.get("title") or "",
            "body": (r.get("body") or "")[:600],
            "error_code": r.get("error_code") or "",
            "alarm_code": r.get("alarm_code") or "",
            "action_taken": (r.get("action_taken") or "")[:300],
            "component_name_raw": r.get("component_name_raw") or "",
            "event_name": r.get("event_name") or "",
            "signal_name": r.get("signal_name") or "",
            "observed_value": r.get("observed_value") or "",
            "observed_unit": r.get("observed_unit") or "",
        }
        for r in batch
    ]
    prompt = (
        "You are a semantic indexing assistant for industrial maintenance logs.\n"
        "For each entry produce a concise English semantic_text for embedding-based search.\n"
        "Capture: component, event type, key measurements/error codes, action taken, outcome.\n"
        "Write 1-3 sentences. Translate multilingual input to English. Clean garbled text.\n"
        "If the body contains stack traces or technical dumps, summarise the key error.\n\n"
        f"Logs:\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
        'Return JSON: {"results": [{"log_id": "...", "semantic_text": "..."}]}'
    )
    resp = client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    return {r["log_id"]: r["semantic_text"] for r in (data.get("results") or [])}


# ── Utility ───────────────────────────────────────────────────────────────────

def _add_flag(row: dict[str, str], flag: str) -> None:
    flags = {f.strip() for f in (row.get("quality_flags") or "").split(",") if f.strip()}
    flags.add(flag)
    row["quality_flags"] = ",".join(sorted(flags))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Normalize machine_logs.csv before embedding")
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--no-backup", action="store_true", help="Skip backup of original CSV")
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 2
    if not args.csv.exists():
        print(f"ERROR: {args.csv} not found", file=sys.stderr)
        return 2

    with args.csv.open("r", encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys()) if rows else []
    print(f"Read {len(rows)} rows from {args.csv}.")

    # ── Pass 1: severity ──────────────────────────────────────────────────────
    n1 = sum(1 for row in rows if _fix_severity(row))
    print(f"Pass 1 (severity):      fixed {n1} rows.")

    # ── Pass 2: component resolution ──────────────────────────────────────────
    client = OpenAI(api_key=OPENAI_API_KEY)
    registry = _build_registry(rows)
    needs_comp = [r for r in rows if _needs_comp_resolution(r)]
    print(f"Pass 2 (component):     {len(needs_comp)} rows need resolution.")
    n2 = 0
    for i in range(0, len(needs_comp), LLM_BATCH):
        batch = needs_comp[i : i + LLM_BATCH]
        resolved = _resolve_comp_batch(client, batch, registry)
        for row in rows:
            if row["log_id"] in resolved:
                row["component_id"] = resolved[row["log_id"]]
                _add_flag(row, "comp_normalized")
                n2 += 1
    print(f"                        resolved {n2} component IDs.")

    # ── Pass 3: semantic_text reconstruction ──────────────────────────────────
    needs_sem = [r for r in rows if _needs_semantic_reconstruction(r)]
    print(f"Pass 3 (semantic_text): {len(needs_sem)} rows need reconstruction.")
    n3 = 0
    for i in range(0, len(needs_sem), LLM_BATCH):
        batch = needs_sem[i : i + LLM_BATCH]
        reconstructed = _reconstruct_sem_batch(client, batch)
        for row in rows:
            if row["log_id"] in reconstructed:
                row["semantic_text"] = reconstructed[row["log_id"]]
                _add_flag(row, "semantic_reconstructed")
                n3 += 1
    print(f"                        reconstructed {n3} semantic_text values.")

    # ── Backup + write ────────────────────────────────────────────────────────
    if not args.no_backup:
        bak = args.csv.with_suffix(".csv.bak")
        shutil.copy2(args.csv, bak)
        print(f"Backup written to {bak}.")

    with args.csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} normalized rows to {args.csv}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
