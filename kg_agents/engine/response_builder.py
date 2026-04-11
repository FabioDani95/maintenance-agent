from __future__ import annotations

from typing import Any

from kg_agents.config import OPENAI_CHAT_MODEL

from .ontology_loader import get_product_metadata


def _meta(product_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return product_meta or get_product_metadata()


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


def low_confidence_response(product_meta: dict[str, Any] | None = None) -> str:
    meta = _meta(product_meta)
    return (
        f"I could not confidently match your description to a known {meta['product_short_name']} "
        "troubleshooting symptom. Please describe what you observe more precisely — for example, "
        "which part is affected, what sound or visual problem you notice, or when the issue occurs."
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
                    lines.append(f"   **Source:** [MANUAL:{src_title}:{src_ref}]")
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
