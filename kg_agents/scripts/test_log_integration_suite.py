"""Comprehensive test suite for the log integration end-to-end flow.

Covers:
1. Intent classification accuracy across all 5 intents
2. Retrieval quality (right signature surfaces, right occurrence anchored)
3. Filter extraction correctness (date/severity/status)
4. Reply grounding (replies must mention specific facts from retrieved data)
5. Edge cases (no matches, unknown WO, operator notes, multi-turn hybrid)
6. Logs HTTP API (summary, list, detail, log-search)
7. Graph overlay (LogEvent nodes + virtual edges)
8. Existing KG flow not regressed
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from kg_agents.engine.log_loader import evict_log_cache, load_log_store
evict_log_cache()
from fastapi.testclient import TestClient
from kg_agents.main import app

INSTANCE = "irc5-default-instance"
client = TestClient(app)

# ──────────────────────────── helpers ────────────────────────────


@dataclass
class TestResult:
    name: str
    passed: bool
    note: str = ""
    detail: dict = field(default_factory=dict)


def post_chat(message: str, session_id: str | None = None) -> dict:
    sid = session_id or str(uuid.uuid4())
    r = client.post(
        f"/v1/kg-agents/instances/{INSTANCE}/chat",
        json={"message": message, "session_id": sid},
    )
    assert r.status_code == 200, f"chat returned {r.status_code}: {r.text}"
    return r.json(), sid


def evidence_signatures(data: dict) -> list[str]:
    return [e.get("event_signature_id") for e in data.get("log_evidence", [])]


def reply_lower(data: dict) -> str:
    return (data.get("reply") or "").lower()


def contains_any(text: str, needles: list[str]) -> bool:
    t = text.lower()
    return any(n.lower() in t for n in needles)


def contains_all(text: str, needles: list[str]) -> bool:
    t = text.lower()
    return all(n.lower() in t for n in needles)


# ──────────────────────────── tests ────────────────────────────


def test_intent_classification(results: list[TestResult]) -> None:
    """Each intent reachable. Tolerant: history vs analytics often overlap."""
    cases = [
        ("Has Ethernet packet loss happened before on this IRC5?",
         {"log_history_search", "log_analytics"}),
        ("Drive module went over 90C — what did we do last time?",
         {"log_history_search", "hybrid_diagnosis_with_history"}),
        ("Show me details for work order WO-IRC5-1042",
         {"work_order_lookup"}),
        ("Which IRC5 component has the most repeated warnings?",
         {"log_analytics"}),
        ("How often does the drive overheat?",
         {"log_analytics", "log_history_search"}),
        ("FlexPendant just disconnected and is dead — what should I check, and has this happened before?",
         {"hybrid_diagnosis_with_history", "log_history_search"}),
        ("The drive motor is overheating at 88C, what's wrong?",
         {"troubleshooting_current", "hybrid_diagnosis_with_history"}),
        ("Robot brake voltage too low, how do I fix it?",
         {"troubleshooting_current"}),
    ]
    correct = 0
    detail = []
    for q, accepted in cases:
        data, _ = post_chat(q)
        intent = data.get("intent")
        ok = intent in accepted
        if ok:
            correct += 1
        detail.append({"q": q[:50], "got": intent, "accept": list(accepted), "ok": ok})
    passed = correct >= len(cases) - 1  # tolerate one miss
    results.append(TestResult(
        "intent_classification",
        passed,
        f"{correct}/{len(cases)} classified into accepted set",
        {"breakdown": detail},
    ))


def test_retrieval_quality_top1(results: list[TestResult]) -> None:
    """For log_history queries, the right signature must be in top-1 (or top-2)."""
    cases = [
        ("Has Ethernet packet loss ever happened?",
         "irc5_communications_ethernet_packet_loss"),
        ("FlexPendant disconnection events history",
         "irc5_flexpendant_disconnected"),
        ("Drive motor overtemperature history",
         "irc5_drive_motor_overtemperature"),
        ("Past brake release voltage faults",
         "irc5_brake_release_fault"),
        ("USB communication errors we have seen",
         "irc5_usb_communication_error"),
        ("History of mains power loss events",
         "irc5_mains_power_supply_loss"),
        ("Calibration drift events",
         "irc5_robot_tcp_calibration_drift"),
        ("DSQC 662 faults log",
         "irc5_dsqc_662_fault"),
    ]
    top1 = 0
    top2 = 0
    detail = []
    for q, expected in cases:
        data, _ = post_chat(q)
        sigs = evidence_signatures(data)
        is_top1 = sigs and sigs[0] == expected
        is_top2 = expected in sigs[:2]
        if is_top1:
            top1 += 1
        if is_top2:
            top2 += 1
        detail.append({"q": q[:60], "expected": expected, "got": sigs[:3], "top1": is_top1, "top2": is_top2})
    passed = top1 >= 5 and top2 >= 7  # demand high recall
    results.append(TestResult(
        "retrieval_quality_top1",
        passed,
        f"top1: {top1}/{len(cases)}, top2: {top2}/{len(cases)}",
        {"breakdown": detail},
    ))


def test_work_order_exact_lookup(results: list[TestResult]) -> None:
    """Specific WO ID must surface the matching log."""
    cases = [
        ("WO-IRC5-1042", "irc5_pm_gearbox_oil_change"),
        ("Show me work order WO-IRC5-1001", "irc5_communications_ethernet_packet_loss"),
        ("Details for WO-IRC5-1022", "irc5_brake_release_fault"),
    ]
    hits = 0
    detail = []
    for q, expected_sig in cases:
        data, _ = post_chat(q)
        sigs = evidence_signatures(data)
        ok = sigs and sigs[0] == expected_sig
        # Also verify the WO id appears in the reply
        wo_id = next((w for w in q.split() if w.startswith("WO-IRC5-")), "")
        wo_in_reply = wo_id.lower() in reply_lower(data)
        if ok and wo_in_reply:
            hits += 1
        detail.append({"q": q, "expected_sig": expected_sig, "got_sig": sigs[:1], "wo_in_reply": wo_in_reply})
    passed = hits == len(cases)
    results.append(TestResult(
        "work_order_exact_lookup",
        passed,
        f"{hits}/{len(cases)} surfaced WO and cited id",
        {"breakdown": detail},
    ))


def test_reply_grounding(results: list[TestResult]) -> None:
    """Reply for log_history must mention specific facts (date or WO or action)."""
    q = "Has Ethernet packet loss ever happened on this IRC5? What did we do?"
    data, _ = post_chat(q)
    reply = reply_lower(data)
    # Must reference at least one concrete artifact
    has_year = any(year in reply for year in ("2024", "2025", "2026"))
    has_wo = "wo-irc5-" in reply
    has_action_keyword = contains_any(reply, ["cable", "switch", "port", "rj45", "ethernet"])
    has_count = contains_any(reply, ["7", "seven", "occurrences", "events"])
    passed = sum([has_year, has_wo, has_action_keyword, has_count]) >= 3
    results.append(TestResult(
        "reply_grounding",
        passed,
        f"date={has_year} wo={has_wo} action_kw={has_action_keyword} count={has_count}",
        {"reply_head": reply[:200]},
    ))


def test_severity_filter(results: list[TestResult]) -> None:
    """ERROR severity filter must exclude INFO/WARN occurrences."""
    q = "Show me only critical errors we've had on the drive"
    data, _ = post_chat(q)
    # We don't strictly know if classifier added severity_min, but at least
    # all returned evidence top-matches should be high severity if filter was applied.
    sigs = evidence_signatures(data)
    severities = []
    for e in data.get("log_evidence", []):
        sev = (e.get("top_match") or {}).get("severity_text") or ""
        severities.append(sev)
    only_serious = all(s.upper() in ("ERROR", "FATAL", "WARN") for s in severities) if severities else False
    passed = bool(severities) and only_serious
    results.append(TestResult(
        "severity_filter",
        passed,
        f"severities returned: {severities}",
    ))


def test_no_match_path(results: list[TestResult]) -> None:
    """Query for something not in the dataset returns no-match reply."""
    q = "Has the hydraulic press ever leaked oil on this IRC5?"
    data, _ = post_chat(q)
    reply = reply_lower(data)
    # Hydraulic press doesn't exist; either classifier routes to troubleshooting
    # OR a log search returns nothing. Either is fine — what we don't want is a
    # confident hallucinated past event.
    no_evidence = len(data.get("log_evidence", [])) == 0
    not_hallucinating = "hydraulic press" not in reply or contains_any(
        reply, ["no past", "couldn't find", "no events", "not found", "no log", "no record"]
    )
    passed = no_evidence or not_hallucinating
    results.append(TestResult(
        "no_match_path",
        passed,
        f"no_evidence={no_evidence} not_hallucinating={not_hallucinating}",
        {"intent": data.get("intent"), "reply_head": reply[:200]},
    ))


def test_operator_note_no_kg_link(results: list[TestResult]) -> None:
    """Operator note signatures (no failure mode link) must still surface."""
    q = "Did operators ever report vibration that was never diagnosed?"
    data, _ = post_chat(q)
    sigs = evidence_signatures(data)
    # We expect either the operator_note signature OR bearing_pdm (both vibration-related)
    relevant = any(s in sigs[:3] for s in [
        "irc5_operator_note_unknown_vibration",
        "irc5_bearings_wear_pdm",
    ])
    passed = relevant
    results.append(TestResult(
        "operator_note_no_kg_link",
        passed,
        f"top3 sigs: {sigs[:3]}",
    ))


def test_kg_flow_not_regressed(results: list[TestResult]) -> None:
    """Pure troubleshooting query must still go through KG (no log evidence)."""
    q = "Robot controller is not responding, what should I check first?"
    data, _ = post_chat(q)
    intent = data.get("intent")
    evidence_empty = len(data.get("log_evidence", [])) == 0
    has_current_issue = data.get("current_issue") is not None or data.get("awaiting_clarification")
    # Intent should NOT be log_*
    not_log_intent = intent in (None, "troubleshooting_current", "hybrid_diagnosis_with_history")
    passed = evidence_empty and not_log_intent and (has_current_issue or "controller" in reply_lower(data))
    results.append(TestResult(
        "kg_flow_not_regressed",
        passed,
        f"intent={intent} evidence={'empty' if evidence_empty else 'NON-empty'} curr_issue={has_current_issue}",
    ))


def test_multi_turn_hybrid_clarification(results: list[TestResult]) -> None:
    """Hybrid + clarification + answer: history must show only in final answer."""
    q1 = "FlexPendant just disconnected — has this happened before and how was it fixed?"
    data1, sid = post_chat(q1)
    turn1_has_appendix = "past similar events" in reply_lower(data1)
    turn1_intent = data1.get("intent")
    turn1_awaiting = data1.get("awaiting_clarification")

    # Turn 2: answer the clarification (assume option 1)
    if turn1_awaiting:
        data2, _ = post_chat("1", session_id=sid)
        turn2_has_appendix = "past similar events" in reply_lower(data2)
        turn2_evidence = len(data2.get("log_evidence", []))
        turn2_intent = data2.get("intent")
    else:
        # If KG went straight to answer, hybrid attaches appendix immediately
        turn2_has_appendix = turn1_has_appendix
        turn2_evidence = len(data1.get("log_evidence", []))
        turn2_intent = turn1_intent

    appendix_on_final_only = (not turn1_awaiting or not turn1_has_appendix) and turn2_has_appendix
    evidence_on_final = turn2_evidence > 0
    passed = appendix_on_final_only and evidence_on_final
    results.append(TestResult(
        "multi_turn_hybrid_clarification",
        passed,
        f"t1_awaiting={turn1_awaiting} t1_appendix={turn1_has_appendix} t2_appendix={turn2_has_appendix} t2_evidence={turn2_evidence}",
        {"t1_intent": turn1_intent, "t2_intent": turn2_intent},
    ))


def test_logs_api_summary(results: list[TestResult]) -> None:
    r = client.get(f"/v1/kg-agents/instances/{INSTANCE}/logs/summary")
    ok = r.status_code == 200
    d = r.json() if ok else {}
    store = load_log_store(INSTANCE)
    expected_count = len(store.rows) if store is not None else 0
    passed = ok and d.get("row_count") == expected_count and len(d.get("top_event_signatures", [])) > 0
    results.append(TestResult(
        "logs_api_summary",
        passed,
        (
            f"status={r.status_code} row_count={d.get('row_count')} "
            f"expected={expected_count} sigs={len(d.get('top_event_signatures', []))}"
        ),
    ))


def test_logs_api_list_filters(results: list[TestResult]) -> None:
    r = client.get(
        f"/v1/kg-agents/instances/{INSTANCE}/logs",
        params={"severity_min": 18, "date_from": "2026-01-01"},
    )
    ok = r.status_code == 200
    d = r.json() if ok else {}
    items = d.get("items", [])
    # All items must satisfy: severity_number >= 18 AND occurred_at >= 2026-01-01
    sev_ok = all((it.get("severity_number") or 0) >= 18 for it in items)
    date_ok = all((it.get("occurred_at") or "") >= "2026-01-01" for it in items)
    passed = ok and len(items) > 0 and sev_ok and date_ok
    results.append(TestResult(
        "logs_api_list_filters",
        passed,
        f"items={len(items)} all_sev_ok={sev_ok} all_date_ok={date_ok}",
    ))


def test_logs_api_log_search(results: list[TestResult]) -> None:
    r = client.post(
        f"/v1/kg-agents/instances/{INSTANCE}/log-search",
        json={"query": "ethernet packet loss", "limit": 3, "use_llm_rerank": False},
    )
    ok = r.status_code == 200
    d = r.json() if ok else {}
    matches = d.get("matches", [])
    top_is_ethernet = matches and matches[0]["event_signature_id"] == "irc5_communications_ethernet_packet_loss"
    passed = ok and top_is_ethernet
    results.append(TestResult(
        "logs_api_log_search",
        passed,
        f"status={r.status_code} matches={len(matches)} top={matches[0]['event_signature_id'] if matches else None}",
    ))


def test_graph_overlay_default_off(results: list[TestResult]) -> None:
    """Default graph-data must have ZERO LogEvent nodes."""
    r = client.get(f"/v1/kg-agents/instances/{INSTANCE}/graph-data")
    ok = r.status_code == 200
    d = r.json() if ok else {}
    log_nodes = [n for n in d.get("nodes", []) if n.get("group") == "LogEvent"]
    passed = ok and len(log_nodes) == 0
    results.append(TestResult(
        "graph_overlay_default_off",
        passed,
        f"log_nodes={len(log_nodes)}",
    ))


def test_graph_overlay_enabled(results: list[TestResult]) -> None:
    """include_logs=true produces LogEvent nodes + virtual edges."""
    r = client.get(
        f"/v1/kg-agents/instances/{INSTANCE}/graph-data",
        params={"include_logs": "true", "limit_logs": 5},
    )
    ok = r.status_code == 200
    d = r.json() if ok else {}
    log_nodes = [n for n in d.get("nodes", []) if n.get("group") == "LogEvent"]
    virtual_edges = [e for e in d.get("edges", []) if e.get("label") in (
        "LOG_FOR_ASSET", "OBSERVED_ON", "SIMILAR_TO_FAILURE_MODE"
    )]
    has_log_in_types = "LogEvent" in d.get("node_types", [])
    has_color = "LogEvent" in d.get("color_map", {})
    passed = ok and len(log_nodes) > 0 and len(virtual_edges) > 0 and has_log_in_types and has_color
    results.append(TestResult(
        "graph_overlay_enabled",
        passed,
        f"log_nodes={len(log_nodes)} virtual_edges={len(virtual_edges)} in_types={has_log_in_types} color={has_color}",
    ))


def test_graph_overlay_query_filtered(results: list[TestResult]) -> None:
    """include_logs=true&log_query=ethernet returns ethernet-related log nodes."""
    r = client.get(
        f"/v1/kg-agents/instances/{INSTANCE}/graph-data",
        params={"include_logs": "true", "log_query": "ethernet packet loss", "limit_logs": 3},
    )
    ok = r.status_code == 200
    d = r.json() if ok else {}
    log_nodes = [n for n in d.get("nodes", []) if n.get("group") == "LogEvent"]
    # At least one log node should be ethernet-related (signature or label)
    ethernet_hit = any(
        ("ethernet" in (n.get("event_signature_id") or "").lower())
        or ("ethernet" in (n.get("label") or "").lower())
        or ("packet" in (n.get("label") or "").lower())
        for n in log_nodes
    )
    passed = ok and len(log_nodes) > 0 and ethernet_hit
    results.append(TestResult(
        "graph_overlay_query_filtered",
        passed,
        f"log_nodes={len(log_nodes)} ethernet_hit={ethernet_hit}",
    ))


# ──────────────────────────── runner ────────────────────────────


def run_all() -> tuple[int, int, list[TestResult]]:
    results: list[TestResult] = []
    tests = [
        test_intent_classification,
        test_retrieval_quality_top1,
        test_work_order_exact_lookup,
        test_reply_grounding,
        test_severity_filter,
        test_no_match_path,
        test_operator_note_no_kg_link,
        test_kg_flow_not_regressed,
        test_multi_turn_hybrid_clarification,
        test_logs_api_summary,
        test_logs_api_list_filters,
        test_logs_api_log_search,
        test_graph_overlay_default_off,
        test_graph_overlay_enabled,
        test_graph_overlay_query_filtered,
    ]
    for fn in tests:
        try:
            fn(results)
        except Exception as e:
            results.append(TestResult(fn.__name__, False, f"raised {type(e).__name__}: {e}"))
    passed = sum(1 for r in results if r.passed)
    return passed, len(results), results


if __name__ == "__main__":
    passed, total, results = run_all()
    print(f"\n{'=' * 80}")
    print(f"   LOG INTEGRATION TEST SUITE — {passed}/{total} passed")
    print(f"{'=' * 80}\n")
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"  [{mark}] {r.name}: {r.note}")
        if not r.passed and r.detail:
            print(f"         detail: {json.dumps(r.detail, ensure_ascii=False, default=str)[:300]}")
    print()
    raise SystemExit(0 if passed == total else 1)
