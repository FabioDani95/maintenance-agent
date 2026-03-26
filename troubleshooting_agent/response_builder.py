from __future__ import annotations

from typing import Any

from config import OPENAI_CHAT_MODEL
from ontology_loader import get_product_metadata


def _meta():
    return get_product_metadata()


def out_of_domain_response() -> str:
    m = _meta()
    return (
        f"I can only assist with troubleshooting and maintenance for the {m['product_name']}. "
        f"Please describe a {m['product_type']} issue, symptom, or maintenance-related problem."
    )


def unclear_domain_response() -> str:
    m = _meta()
    return (
        f"Could you describe the issue you are experiencing with your {m['product_name']} "
        "in more detail? For example, what do you observe, hear, or notice during operation?"
    )


def low_confidence_response() -> str:
    m = _meta()
    return (
        f"I could not confidently match your description to a known {m['product_short_name']} "
        "troubleshooting symptom. Please describe what you observe more precisely — for example, "
        "which part is affected, what sound or visual problem you notice, or when the issue occurs."
    )


def _group_paths_by_failure_mode(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group paths so each failure mode appears once with all its actions."""
    from collections import OrderedDict
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for p in paths:
        fm_id = p["failure_mode_id"]
        if fm_id not in groups:
            groups[fm_id] = {
                "failure_mode_id": fm_id,
                "failure_mode_name": p["failure_mode_name"],
                "component_id": p.get("component_id", ""),
                "component_name": p.get("component_name", ""),
                "actions": [],
            }
        groups[fm_id]["actions"].append({
            "action_id": p["action_id"],
            "action_name": p["action_name"],
            "instruction_text": p.get("instruction_text", ""),
            "source_title": p.get("source_title", ""),
            "source_reference": p.get("source_reference", ""),
        })
    return list(groups.values())



def _render_answer(grouped: list[dict[str, Any]]) -> str:
    """Render troubleshooting answer directly from grouped graph data — no LLM."""
    sections: list[str] = []
    for i, fm in enumerate(grouped, start=1):
        lines: list[str] = []
        prefix = f"{i}. " if len(grouped) > 1 else ""
        lines.append(f"{prefix}**Possible issue:** {fm['failure_mode_name']}")
        if fm.get("component_name"):
            lines.append(f"**Affected component:** {fm['component_name']}")
        lines.append("")
        lines.append("**Recommended corrective actions:**")
        lines.append("")
        # Deduplicate actions by action_id while preserving order
        seen_acts: set[str] = set()
        action_num = 1
        last_source: tuple[str, str] = ("", "")
        for act in fm.get("actions", []):
            if act["action_id"] in seen_acts:
                continue
            seen_acts.add(act["action_id"])
            lines.append(f"{action_num}. **{act['action_name']}**")
            if act.get("instruction_text"):
                # Indent instruction text
                for line in act["instruction_text"].splitlines():
                    lines.append(f"   {line}")
            # Per-action source (only emit if different from previous)
            src_title = act.get("source_title", "")
            src_ref = act.get("source_reference", "")
            if src_title and (src_title, src_ref) != last_source:
                if src_ref and src_ref.startswith("http"):
                    lines.append(f"   **Source:** [{src_title}]({src_ref})")
                elif src_ref:
                    lines.append(f"   **Source:** [MANUAL:{src_title}:{src_ref}]")
                else:
                    lines.append(f"   **Source:** {src_title}")
                last_source = (src_title, src_ref)
            lines.append("")
            action_num += 1

        sections.append("\n".join(lines).rstrip())

    result = "\n\n".join(sections)

    return result


def format_answer(paths: list[dict[str, Any]], user_message: str, model: str = OPENAI_CHAT_MODEL) -> str:
    """Render troubleshooting answer directly from graph data — no LLM call."""
    grouped = _group_paths_by_failure_mode(paths)
    return _render_answer(grouped)


def format_answer_single_group(paths: list[dict[str, Any]], user_message: str, model: str = OPENAI_CHAT_MODEL) -> str:
    """Render answer for a single failure-mode group — no LLM call."""
    grouped = _group_paths_by_failure_mode(paths)
    return _render_answer(grouped)


def format_clarification(paths: list[dict[str, Any]], user_message: str, model: str = OPENAI_CHAT_MODEL) -> str:
    """Generate a targeted clarifying question to disambiguate multiple paths.

    Fully deterministic — no LLM call. Builds the question directly from the
    failure mode names present in the graph paths.
    """
    # Deduplicate while preserving insertion order
    seen: set[str] = set()
    failure_modes: list[str] = []
    for p in paths:
        name = p.get("failure_mode_name", "")
        if name and name not in seen:
            seen.add(name)
            failure_modes.append(name)

    if not failure_modes:
        return "Could you describe the issue in more detail?"

    if len(failure_modes) == 1:
        return f"Is the issue related to **{failure_modes[0]}**?"

    # Build "A, B, or C?" style question
    options = ", ".join(f"**{fm}**" for fm in failure_modes[:-1])
    options += f", or **{failure_modes[-1]}**"
    return f"Is the issue related to {options}?"
