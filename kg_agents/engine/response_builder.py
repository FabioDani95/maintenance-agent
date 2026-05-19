from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from kg_agents.config import OPENAI_API_KEY
from kg_agents.config import OPENAI_CHAT_MODEL

from .ontology_loader import get_product_metadata


_PAGE_REF_RE = re.compile(r"\b(\d+)\b")
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _meta(product_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return product_meta or get_product_metadata()


def _manual_page_reference(source_reference: str) -> str | None:
    stripped = (source_reference or "").strip()
    if not stripped or stripped.startswith("http"):
        return None
    if stripped.isdigit():
        return stripped
    match = _PAGE_REF_RE.search(stripped)
    if match:
        return match.group(1)
    return None


def out_of_domain_response(product_meta: dict[str, Any] | None = None) -> str:
    meta = _meta(product_meta)
    return (
        f"I can only assist with troubleshooting and maintenance for the {meta['product_name']}. "
        f"Please describe a {meta['product_type']} issue, symptom, or maintenance-related problem."
    )


def unclear_domain_response(product_meta: dict[str, Any] | None = None) -> str:
    meta = _meta(product_meta)
    return (
        f"Could you describe the issue you are experiencing with your {meta['product_name']} "
        "in more detail? For example, what do you observe, hear, or notice during operation?"
    )


def low_confidence_response(
    product_meta: dict[str, Any] | None = None,
    unmatched_terms: list[str] | None = None,
) -> str:
    meta = _meta(product_meta)
    if unmatched_terms:
        terms = ", ".join(f"**{term}**" for term in unmatched_terms[:3])
        return (
            f"I could not find a sufficiently close troubleshooting match in the "
            f"{meta['product_short_name']} knowledge base for {terms}. Please describe the observed "
            "symptom more precisely, or rephrase it using the machine behaviour you can see directly."
        )
    return (
        f"I could not find a sufficiently close troubleshooting match in the {meta['product_short_name']} "
        "knowledge base for your description. Please describe what you observe more precisely — "
        "for example, which part is affected, what sound or visual problem you notice, or when the "
        "issue occurs."
    )


def _group_paths_by_failure_mode(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from collections import OrderedDict

    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for path in paths:
        fm_id = path["failure_mode_id"]
        if fm_id not in groups:
            groups[fm_id] = {
                "failure_mode_id": fm_id,
                "failure_mode_name": path["failure_mode_name"],
                "component_id": path.get("component_id", ""),
                "component_name": path.get("component_name", ""),
                "actions": [],
            }
        groups[fm_id]["actions"].append({
            "action_id": path["action_id"],
            "action_name": path["action_name"],
            "instruction_text": path.get("instruction_text", ""),
            "source_title": path.get("source_title", ""),
            "source_reference": path.get("source_reference", ""),
        })
    return list(groups.values())


def _render_answer(grouped: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for idx, failure_mode in enumerate(grouped, start=1):
        lines: list[str] = []
        prefix = f"{idx}. " if len(grouped) > 1 else ""
        lines.append(f"{prefix}**Possible issue:** {failure_mode['failure_mode_name']}")
        if failure_mode.get("component_name"):
            lines.append(f"**Affected component:** {failure_mode['component_name']}")
        lines.append("")
        lines.append("**Recommended corrective actions:**")
        lines.append("")

        seen_actions: set[str] = set()
        action_num = 1
        last_source: tuple[str, str] = ("", "")
        for action in failure_mode.get("actions", []):
            if action["action_id"] in seen_actions:
                continue
            seen_actions.add(action["action_id"])
            lines.append(f"{action_num}. **{action['action_name']}**")
            if action.get("instruction_text"):
                for line in action["instruction_text"].splitlines():
                    lines.append(f"   {line}")
            src_title = action.get("source_title", "")
            src_ref = action.get("source_reference", "")
            if src_title and (src_title, src_ref) != last_source:
                if src_ref and src_ref.startswith("http"):
                    lines.append(f"   **Source:** [{src_title}]({src_ref})")
                elif src_ref:
                    page_ref = _manual_page_reference(src_ref)
                    if page_ref:
                        lines.append(f"   **Source:** [MANUAL:{src_title}:{page_ref}]")
                    else:
                        lines.append(f"   **Source:** {src_title} ({src_ref})")
                else:
                    lines.append(f"   **Source:** {src_title}")
                last_source = (src_title, src_ref)
            lines.append("")
            action_num += 1

        sections.append("\n".join(lines).rstrip())

    return "\n\n".join(sections)


def format_answer(
    paths: list[dict[str, Any]],
    user_message: str,
    model: str = OPENAI_CHAT_MODEL,
    product_meta: dict[str, Any] | None = None,
) -> str:
    grouped = _group_paths_by_failure_mode(paths)
    return _render_answer(grouped)


def format_answer_single_group(
    paths: list[dict[str, Any]],
    user_message: str,
    model: str = OPENAI_CHAT_MODEL,
    product_meta: dict[str, Any] | None = None,
) -> str:
    grouped = _group_paths_by_failure_mode(paths)
    return _render_answer(grouped)


def _source_citation(source_title: str | None, source_reference: str | None) -> str:
    title = (source_title or "").strip()
    ref = (source_reference or "").strip()
    if not title:
        return ""
    if ref.startswith("http"):
        return f"[{title}]({ref})"
    if ref:
        page_ref = _manual_page_reference(ref)
        if page_ref:
            return f"[MANUAL:{title}:{page_ref}]"
        return f"{title} ({ref})"
    return title


def _unique_kg_items(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kg_items: list[dict[str, Any]] = []
    seen_actions: set[str] = set()
    for path in paths:
        action_id = str(path.get("action_id") or "")
        if action_id and action_id in seen_actions:
            continue
        if action_id:
            seen_actions.add(action_id)
        kg_items.append({
            "failure_mode_id": path.get("failure_mode_id"),
            "failure_mode_name": path.get("failure_mode_name"),
            "component": path.get("component_name") or path.get("component_id"),
            "symptom": path.get("symptom_name"),
            "action_id": action_id,
            "action_name": path.get("action_name"),
            "instruction_text": path.get("instruction_text"),
            "manual_source": path.get("source_title"),
            "manual_reference": path.get("source_reference"),
            "manual_citation": _source_citation(
                str(path.get("source_title") or ""),
                str(path.get("source_reference") or ""),
            ),
        })
    return kg_items


def _slim_log_evidence(log_evidence: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    evidence_items: list[dict[str, Any]] = []
    for item in (log_evidence or [])[:3]:
        top = item.get("top_match") or {}
        evidence_items.append({
            "event_signature_id": item.get("event_signature_id"),
            "matched_occurrence_count": item.get("matched_occurrence_count"),
            "linked_failure_mode_id": item.get("linked_failure_mode_id"),
            "first_seen_at": item.get("first_seen_at"),
            "last_seen_at": item.get("last_seen_at"),
            "top_match": {
                "date": str(top.get("occurred_at") or "")[:10],
                "severity": top.get("severity_text"),
                "title": top.get("title"),
                "body": top.get("body"),
                "action_taken": top.get("action_taken"),
                "outcome": top.get("outcome"),
                "work_order_id": top.get("work_order_id"),
                "component": top.get("component_name_raw"),
            },
        })
    return evidence_items


def build_prioritized_solve_context(
    *,
    paths: list[dict[str, Any]],
    user_message: str,
    log_evidence: list[dict[str, Any]] | None,
    product_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = _meta(product_meta)
    return {
        "product": {
            "name": meta.get("product_name"),
            "short_name": meta.get("product_short_name"),
            "type": meta.get("product_type"),
        },
        "user_problem": user_message,
        "kg_manual_evidence": _unique_kg_items(paths),
        "past_event_evidence": _slim_log_evidence(log_evidence),
    }


def _default_solve_decision(context: dict[str, Any]) -> dict[str, Any]:
    kg_items = context.get("kg_manual_evidence") or []
    logs = context.get("past_event_evidence") or []
    first = kg_items[0] if kg_items else {}
    failure = first.get("failure_mode_name") or "the matched failure mode"
    action = first.get("action_name") or "the first manual corrective action"
    assessment = [
        f"The KG/manual match points to **{failure}**.",
        "Treat this as the primary troubleshooting path unless direct observations contradict it.",
    ]
    if logs:
        top = logs[0]
        count = int(top.get("matched_occurrence_count") or 0)
        signature = top.get("event_signature_id") or "similar event"
        if count:
            assessment.append(
                f"Past events provide supporting context: `{signature}` had {count} matching event"
                f"{'s' if count != 1 else ''} for this query."
            )
        else:
            assessment.append(f"Past events provide supporting context through `{signature}`.")
    else:
        assessment.append("No similar past event evidence was retrieved for this turn.")
    return {
        "assessment": assessment[:3],
        "priority_action_id": first.get("action_id") or "",
        "priority": action,
        "priority_reason": "It is the first corrective action returned by the KG/manual path for the matched issue.",
        "evidence_notes": [
            f"Manual/KG evidence points to **{failure}**.",
            "Past-event evidence is supporting context only; the first action remains grounded in the manual path.",
        ],
    }


def _coerce_list(value: Any, limit: int) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()][:limit]
    if isinstance(value, str) and value.strip():
        return [value.strip()][:limit]
    return []


def _llm_solve_decision(context: dict[str, Any], model: str) -> dict[str, Any]:
    action_ids = [
        str(item.get("action_id") or "")
        for item in context.get("kg_manual_evidence", [])
        if item.get("action_id")
    ]
    system = (
        "You are a prioritisation component for an industrial troubleshooting assistant. "
        "The user has a CURRENT machine problem. Return JSON only. Use only the provided "
        "KG/manual evidence and past-event evidence. Do not add new checks, tools, causes, "
        "measurements, or safety steps that are not implied by the provided evidence.\n\n"
        "Schema:\n"
        "{"
        "\"assessment\": [\"2-3 short bullets\"], "
        "\"priority_action_id\": \"one of the provided action ids, or empty string\", "
        "\"priority\": \"one short sentence naming what to do first\", "
        "\"priority_reason\": \"one short grounded rationale\", "
        "\"evidence_notes\": [\"2-4 concise facts that support the recommendation\"]"
        "}\n\n"
        "Do not ask the user questions. Do not tell the user to report back. "
        "Give a passive expert recommendation: what to do first, why, and the "
        "evidence behind it.\n\n"
        f"Valid action ids: {action_ids}"
    )
    resp = _get_client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
    )
    parsed = json.loads(resp.choices[0].message.content or "{}")
    decision = _default_solve_decision(context)
    assessment = _coerce_list(parsed.get("assessment"), 3)
    if assessment:
        decision["assessment"] = assessment
    priority_action_id = str(parsed.get("priority_action_id") or "").strip()
    if priority_action_id in action_ids:
        decision["priority_action_id"] = priority_action_id
    priority = str(parsed.get("priority") or "").strip()
    if priority:
        decision["priority"] = priority
    priority_reason = str(parsed.get("priority_reason") or "").strip()
    if priority_reason:
        decision["priority_reason"] = priority_reason
    evidence_notes = _coerce_list(parsed.get("evidence_notes"), 4)
    if evidence_notes:
        decision["evidence_notes"] = evidence_notes
    return decision


def _order_actions(kg_items: list[dict[str, Any]], priority_action_id: str) -> list[dict[str, Any]]:
    if not priority_action_id:
        return kg_items
    return sorted(
        kg_items,
        key=lambda item: 0 if item.get("action_id") == priority_action_id else 1,
    )


def _clean_instruction_line(line: str) -> str:
    return re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line or "").strip()


def _render_prioritized_solve_answer(context: dict[str, Any], decision: dict[str, Any]) -> str:
    kg_items = context.get("kg_manual_evidence") or []
    logs = context.get("past_event_evidence") or []
    ordered_actions = _order_actions(kg_items, str(decision.get("priority_action_id") or ""))

    lines: list[str] = ["**Recommendation**"]
    for bullet in _coerce_list(decision.get("assessment"), 3):
        lines.append(f"- {bullet}")

    lines.extend(["", "**Do first**"])
    priority = str(decision.get("priority") or "").strip()
    reason = str(decision.get("priority_reason") or "").strip()
    if priority:
        lines.append(f"- {priority}")
    if reason:
        lines.extend(["", "**Why**", f"- {reason}"])

    lines.extend(["", "**Grounded action path**"])
    if ordered_actions:
        for idx, action in enumerate(ordered_actions, start=1):
            action_name = action.get("action_name") or action.get("action_id") or "Manual corrective action"
            lines.append(f"{idx}. **{action_name}**")
            instruction = str(action.get("instruction_text") or "").strip()
            if instruction:
                for instruction_line in instruction.splitlines():
                    clean = _clean_instruction_line(instruction_line)
                    if clean:
                        lines.append(f"   - {clean}")
            citation = action.get("manual_citation") or ""
            if citation:
                lines.append(f"   - Source: {citation}")
    else:
        lines.append("1. **Collect more observable detail**")
        lines.append("   - The KG/manual path did not provide a concrete corrective action.")

    if logs:
        top = logs[0]
        top_match = top.get("top_match") or {}
        if top_match.get("action_taken") or top_match.get("outcome"):
            lines.append(f"{len(ordered_actions) + 1}. **Use the closest past event as supporting context**")
            if top_match.get("title"):
                lines.append(f"   - Event: {top_match['title']}")
            if top_match.get("action_taken"):
                lines.append(f"   - Past action: {top_match['action_taken']}")
            if top_match.get("outcome"):
                lines.append(f"   - Past outcome: {top_match['outcome']}")
            if top_match.get("work_order_id") or top_match.get("date"):
                parts = [str(part) for part in (top_match.get("date"), top_match.get("work_order_id")) if part]
                lines.append(f"   - Reference: {' / '.join(parts)}")

    lines.extend(["", "**Evidence**", "- **Manual/KG:**"])
    if kg_items:
        seen_citations: set[str] = set()
        for item in kg_items:
            citation = str(item.get("manual_citation") or "").strip()
            if not citation or citation in seen_citations:
                continue
            seen_citations.add(citation)
            action = item.get("action_name") or "corrective action"
            lines.append(f"  - Manual source for **{action}**: {citation}")
    else:
        lines.append("  - No manual/KG evidence available.")

    lines.append("- **Past events:**")
    if logs:
        for item in logs:
            top_match = item.get("top_match") or {}
            matched_count = int(item.get("matched_occurrence_count") or 0)
            bits = []
            if matched_count:
                bits.append(
                    f"{matched_count} matching event"
                    f"{'s' if matched_count != 1 else ''}"
                )
            else:
                bits.append("closest matching event")
            if top_match.get("date"):
                bits.append(str(top_match["date"]))
            if top_match.get("work_order_id"):
                bits.append(str(top_match["work_order_id"]))
            if top_match.get("title"):
                bits.append(str(top_match["title"]))
            if not bits:
                bits.append("closest retrieved past event")
            lines.append("  - " + " - ".join(bits))
    else:
        lines.append("  - No similar past event evidence retrieved.")

    notes = _coerce_list(decision.get("evidence_notes"), 4)
    if notes:
        lines.extend(["", "**Notes**"])
    for item in notes:
        lines.append(f"- {item}")

    return "\n".join(lines)


def compose_prioritized_solve_answer(
    *,
    paths: list[dict[str, Any]],
    user_message: str,
    log_evidence: list[dict[str, Any]] | None,
    model: str,
    product_meta: dict[str, Any] | None = None,
) -> str:
    """Compose an opinionated non-fast troubleshooting answer.

    Fast mode intentionally stays deterministic. Guided/non-fast mode can spend
    one model call to reconcile the manual/KG path with similar past events and
    make a clear prioritisation call for the technician.
    """
    if not paths:
        return ""

    context = build_prioritized_solve_context(
        paths=paths,
        user_message=user_message,
        log_evidence=log_evidence,
        product_meta=product_meta,
    )
    try:
        decision = _llm_solve_decision(context, model)
    except Exception:
        decision = _default_solve_decision(context)
    return _render_prioritized_solve_answer(context, decision)


def format_clarification(
    paths: list[dict[str, Any]],
    user_message: str,
    model: str = OPENAI_CHAT_MODEL,
    product_meta: dict[str, Any] | None = None,
) -> str:
    seen: set[str] = set()
    failure_modes: list[str] = []
    for path in paths:
        name = path.get("failure_mode_name", "")
        if name and name not in seen:
            seen.add(name)
            failure_modes.append(name)

    if not failure_modes:
        return "Could you describe the issue in more detail?"

    if len(failure_modes) == 1:
        return f"Is the issue related to **{failure_modes[0]}**?"

    options = ", ".join(f"**{fm}**" for fm in failure_modes[:-1])
    options += f", or **{failure_modes[-1]}**"
    return f"Is the issue related to {options}?"
