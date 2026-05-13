from __future__ import annotations

import re
from typing import Any

from kg_agents.config import OPENAI_CHAT_MODEL
from kg_agents.models import CurrentIssue

from .ontology_loader import get_product_metadata


_PAGE_REF_RE = re.compile(r"\b(\d+)\b")


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


_ACKS = {
    "it": ("Capito.", "Ok, resto sul punto.", "Chiaro."),
    "en": ("Understood.", "Got it.", "Clear."),
}
_CONTEXT_PREFIX = {
    "it": "Tengo il contesto precedente",
    "en": "Keeping the previous context",
}
_DIAGNOSIS_HEADING = {
    "it": "**Diagnosi / evidenza**",
    "en": "**Diagnosis / evidence**",
}
_NEXT_STEP_LABEL = {
    "it": "**Prossimo passo pratico:**",
    "en": "**Practical next step:**",
}
_QUESTION_LABEL = {
    "it": "**Domanda breve:**",
    "en": "**Short question:**",
}


def _variant(options: tuple[str, ...], seed: str) -> str:
    if not options:
        return ""
    return options[sum(ord(ch) for ch in seed) % len(options)]


def _language(response_context: dict[str, str] | None, user_message: str) -> str:
    if response_context and response_context.get("language") in {"it", "en"}:
        return response_context["language"]
    if re.search(r"\b(cosa|come|guasto|errore|controllo|risolvo|macchina|quello)\b", user_message, re.I):
        return "it"
    return "en"


def _context_line(lang: str, response_context: dict[str, str] | None, current_issue: CurrentIssue | None) -> str:
    component = ""
    if current_issue:
        component = current_issue.component_name or current_issue.component_id
    if not component and response_context:
        component = response_context.get("component", "")
    if not component:
        return ""
    if lang == "it":
        return f"{_CONTEXT_PREFIX[lang]} sul componente **{component}**."
    return f"{_CONTEXT_PREFIX[lang]} on **{component}**."


def _next_step(lang: str, intent: str | None, current_issue: CurrentIssue | None) -> str:
    if current_issue and current_issue.action_options:
        action = current_issue.action_options[0].action_name
        if lang == "it":
            return f"parti dalla prima azione elencata (**{action}**) e registra l'esito."
        return f"start with the first listed action (**{action}**) and record the outcome."

    if intent == "log_analytics":
        return "usa il pattern piu ricorrente per decidere quale componente verificare per primo." if lang == "it" else "use the most recurring pattern to decide which component to check first."
    if intent in {"log_history_search", "work_order_lookup", "hybrid_diagnosis_with_history"}:
        return "confronta il caso piu simile con il sintomo attuale prima di cambiare intervento." if lang == "it" else "compare the closest past case with the current symptom before changing intervention."
    return "rispondi con il dettaglio osservabile piu specifico se il match non torna." if lang == "it" else "reply with the most specific observable detail if the match is off."


def _question(lang: str, intent: str | None, current_issue: CurrentIssue | None) -> str:
    if current_issue:
        return "dopo questo controllo il sintomo cambia?" if lang == "it" else "after this check, does the symptom change?"
    if intent == "log_analytics":
        return "vuoi filtrare per periodo o componente?" if lang == "it" else "do you want to filter by time range or component?"
    if intent in {"log_history_search", "work_order_lookup", "hybrid_diagnosis_with_history"}:
        return "e lo stesso comportamento che vedi ora?" if lang == "it" else "is this the same behavior you see now?"
    return "quale componente o segnale osservi direttamente?" if lang == "it" else "which component or signal do you observe directly?"


def add_conversational_structure(
    reply: str,
    *,
    user_message: str,
    session_id: str,
    mode: str,
    intent: str | None = None,
    response_context: dict[str, str] | None = None,
    current_issue: CurrentIssue | None = None,
    awaiting_clarification: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Wrap deterministic technical output with a thin conversational frame.

    The original diagnostic body is kept verbatim under the evidence heading;
    this function only adds acknowledgement, continuity, next-step, and a short
    follow-up question.
    """
    body = (reply or "").strip()
    if not body:
        return reply, {"follow_up_added": False, "context_reference_added": False}

    lang = _language(response_context, user_message)
    seed = f"{session_id}:{user_message}:{mode}"
    ack = _variant(_ACKS[lang], seed)
    context = _context_line(lang, response_context, current_issue)

    if awaiting_clarification:
        intro = " ".join(part for part in (ack, context) if part)
        if lang == "it":
            structured = f"{intro} Per evitare una diagnosi fuori bersaglio:\n\n{body}"
        else:
            structured = f"{intro} To keep the diagnosis focused:\n\n{body}"
        return structured.strip(), {
            "follow_up_added": True,
            "context_reference_added": bool(context),
            "language": lang,
        }

    next_step = _next_step(lang, intent, current_issue)
    question = _question(lang, intent, current_issue)
    intro = " ".join(part for part in (ack, context) if part)
    structured = (
        f"{intro}\n\n"
        f"{_DIAGNOSIS_HEADING[lang]}\n\n"
        f"{body}\n\n"
        f"{_NEXT_STEP_LABEL[lang]} {next_step}\n\n"
        f"{_QUESTION_LABEL[lang]} {question}"
    )
    return structured.strip(), {
        "follow_up_added": True,
        "context_reference_added": bool(context),
        "language": lang,
    }


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
