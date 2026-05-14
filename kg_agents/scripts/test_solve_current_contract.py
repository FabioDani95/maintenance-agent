"""Contract checks for the solve-current-problem chat behaviour.

This is intentionally not a wording snapshot test. The non-fast branch may use
an LLM to choose rationale text, but the neurosymbolic contract must remain
stable: same problem class -> same KG issue/action, same evidence structure,
same manual citation, and no unsupported "previous context" framing.

Usage:
    python -m kg_agents.scripts.test_solve_current_contract
    python -m kg_agents.scripts.test_solve_current_contract --mode non-fast --runs 3
"""
from __future__ import annotations

import argparse
import sys
import uuid

from fastapi.testclient import TestClient

from kg_agents.main import app


INSTANCE = "irc5-default-instance"
EXPECTED_FAILURE_MODE = "fm_components_overheated"
EXPECTED_ACTION = "ca_wait_until_component_has_cooled"
EXPECTED_MANUAL_CITATION = "[MANUAL:IRC5:23]"
EXPECTED_LOG_SIGNATURE = "irc5_drive_motor_overtemperature"

PARAPHRASES = [
    "The drive motor is overheating at 88C. What should I check first?",
    "The IRC5 drive motor is overheating during operation. Prioritize the checks.",
    "Drive motor overheating on the IRC5 controller. Give me a troubleshooting plan.",
]


def _post_chat(client: TestClient, message: str, mode: str, run_idx: int) -> dict:
    response = client.post(
        f"/v1/kg-agents/instances/{INSTANCE}/chat",
        json={
            "message": message,
            "mode": mode,
            "session_id": f"solve-contract-{run_idx}-{uuid.uuid4()}",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _contract_tuple(data: dict) -> tuple[str, str, str, str]:
    current_issue = data.get("current_issue") or {}
    actions = current_issue.get("action_options") or []
    evidence = data.get("log_evidence") or []
    return (
        data.get("behavior_mode") or "",
        current_issue.get("failure_mode_id") or "",
        actions[0].get("action_id") if actions else "",
        evidence[0].get("event_signature_id") if evidence else "",
    )


def run(mode: str, runs: int) -> int:
    client = TestClient(app)
    failures: list[str] = []
    observed: list[tuple[str, str, str, str]] = []

    for run_idx in range(runs):
        for message in PARAPHRASES:
            data = _post_chat(client, message, mode, run_idx)
            reply = data.get("reply") or ""
            contract = _contract_tuple(data)
            observed.append(contract)

            if contract[0] != "solve_current_problem":
                failures.append(f"wrong behavior_mode for {message!r}: {contract[0]}")
            if contract[1] != EXPECTED_FAILURE_MODE:
                failures.append(f"wrong failure mode for {message!r}: {contract[1]}")
            if contract[2] != EXPECTED_ACTION:
                failures.append(f"wrong first action for {message!r}: {contract[2]}")
            if "previous context" in reply.lower():
                failures.append(f"reply implies previous context for {message!r}")
            if "Components Overheated ->" in reply:
                failures.append(f"reply exposes raw KG path wording for {message!r}")
            if EXPECTED_MANUAL_CITATION not in reply:
                failures.append(f"missing manual citation for {message!r}")

            if mode == "non-fast":
                for section in ("**Assessment**", "**Priority**", "**Action Plan**", "**Evidence Used**", "**Report Back**"):
                    if section not in reply:
                        failures.append(f"missing {section} for {message!r}")
                if contract[3] != EXPECTED_LOG_SIGNATURE:
                    failures.append(f"wrong top log signature for {message!r}: {contract[3]}")
                if "15 occurrences" in reply or "15 occurrence" in reply:
                    failures.append(f"reply exposes signature-total count as query evidence for {message!r}")

    unique_contracts = set(observed)
    if len(unique_contracts) != 1 and mode == "fast":
        failures.append(f"paraphrases did not converge to one fast contract: {sorted(unique_contracts)}")

    print("Observed contracts:")
    for item in observed:
        print("  ", item)

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(" -", failure)
        return 1

    print(f"\nPASS: solve-current contract stable for mode={mode}, runs={runs}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fast", "non-fast"), default="fast")
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    return run(args.mode, args.runs)


if __name__ == "__main__":
    sys.exit(main())
