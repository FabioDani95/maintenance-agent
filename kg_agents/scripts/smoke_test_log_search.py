"""Smoke test for hybrid log retrieval against the IRC5 seed.

Runs a battery of canonical queries that the design doc lists as the kinds of
historical questions log integration must handle, and prints the top matches.
Intended for manual inspection — not a unit test.

Usage:
    python -m kg_agents.scripts.smoke_test_log_search
    python -m kg_agents.scripts.smoke_test_log_search --no-rerank
    python -m kg_agents.scripts.smoke_test_log_search --summary
"""
from __future__ import annotations

import argparse
import json
import sys

from kg_agents.engine.log_search import search_logs, summarize_logs

INSTANCE_ID = "irc5-default-instance"

QUERIES = [
    {
        "query": "Has Ethernet packet loss happened before on this IRC5?",
        "expect_signature": "irc5_communications_ethernet_packet_loss",
    },
    {
        "query": "FlexPendant lost connection during operation",
        "expect_signature": "irc5_flexpendant_disconnected",
    },
    {
        "query": "What did we do last time the drive module overheated?",
        "expect_signature": "irc5_drive_motor_overtemperature",
    },
    {
        "query": "Show me the calibration drift events after a collision",
        "expect_signature": "irc5_robot_tcp_calibration_drift",
    },
    {
        "query": "WO-IRC5-1042",
        "expect_signature": None,  # exact-match scenario, sparse should pick it
    },
    {
        "query": "Brake release voltage low on axis",
        "expect_signature": "irc5_brake_release_fault",
    },
    {
        "query": "USB stick not detected during backup",
        "expect_signature": "irc5_usb_communication_error",
    },
    {
        "query": "Operator reported strange vibration — was it ever investigated?",
        "expect_signature": "irc5_operator_note_unknown_vibration",
    },
    {
        "query": "Has the controller ever lost mains power?",
        "expect_signature": "irc5_mains_power_supply_loss",
    },
    {
        "query": "Routine gearbox oil change on axis 1",
        "expect_signature": "irc5_pm_gearbox_oil_change",
    },
]


def short_repr(row: dict) -> str:
    return (
        f"{row.get('log_id'):<15} {row.get('occurred_at','')[:19]} "
        f"{row.get('severity_text',''):<5} {row.get('title','')[:80]}"
    )


def run_queries(use_rerank: bool) -> int:
    print(f"\n{'='*88}")
    print(f"  HYBRID LOG SEARCH SMOKE TEST  (rerank={use_rerank})")
    print(f"{'='*88}\n")

    hits = 0
    for case in QUERIES:
        q = case["query"]
        expected = case["expect_signature"]
        result = search_logs(q, INSTANCE_ID, limit=3, use_llm_rerank=use_rerank)

        print(f"Q: {q}")
        diag = result.get("diagnostics", {})
        print(f"   diagnostics: dense={diag.get('dense_candidates')} "
              f"sparse={diag.get('sparse_candidates')} "
              f"fused={diag.get('fused_candidates')} "
              f"rerank={diag.get('rerank_used')}")

        if not result["matches"]:
            print("   (no matches)")
            print()
            continue

        for i, m in enumerate(result["matches"], 1):
            marker = ""
            if expected and m["event_signature_id"] == expected and i == 1:
                marker = " ✓ TOP"
                hits += 1
            elif expected and m["event_signature_id"] == expected:
                marker = " ✓ in top-3"
            print(f"   {i}. score={m['score']:.3f}  "
                  f"sig={m['event_signature_id']}{marker}")
            print(f"      top:    {short_repr(m['top_match_log'])}")
            recent = m["most_recent_log"]
            if recent.get("log_id") != m["top_match_log"].get("log_id"):
                print(f"      recent: {short_repr(recent)}")
            print(f"      occurrences={m['occurrence_count']}  "
                  f"fm={m['linked_failure_mode_id'] or '-'}  "
                  f"sym={m['linked_symptom_id'] or '-'}")
            if m.get("rerank_rationale"):
                print(f"      why: {m['rerank_rationale']}")
        print()

    print(f"\nResult: {hits}/{sum(1 for c in QUERIES if c['expect_signature'])} "
          f"queries hit expected signature at top-1")
    return 0


def run_summary() -> int:
    print(f"\n{'='*88}")
    print(f"  LOG SUMMARY  (instance={INSTANCE_ID})")
    print(f"{'='*88}\n")
    summary = summarize_logs(INSTANCE_ID)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--no-rerank", action="store_true")
    p.add_argument("--summary", action="store_true")
    args = p.parse_args()

    if args.summary:
        return run_summary()
    return run_queries(use_rerank=not args.no_rerank)


if __name__ == "__main__":
    sys.exit(main())
