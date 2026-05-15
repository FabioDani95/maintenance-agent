from __future__ import annotations

import re
from typing import Any

from kg_agents.engine.embeddings import get_text_embeddings
from kg_agents.engine.ontology_loader import OntologyIndex
from kg_agents.engine.query_alignment import describe_group_alignment
from kg_agents.engine.similarity import cosine_similarity

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ORDINAL_PATTERNS = {
    0: (r"\b1\b", r"\bone\b", r"\bfirst\b"),
    1: (r"\b2\b", r"\btwo\b", r"\bsecond\b"),
    2: (r"\b3\b", r"\bthree\b", r"\bthird\b"),
}
# Phrases that should map directly to an "is_opt_out" option when one is
# present, regardless of lexical token overlap with the other options.
_OPT_OUT_PHRASES = (
    "none of these",
    "none of those",
    "neither",
    "show me past events",
    "show past events",
)
_ASK_CLARIFICATION_MAX_SCORE_GAP = 0.75
_ASK_CLARIFICATION_MIN_RATIO = 0.88
_ANSWER_MIN_SIMILARITY = 0.24
_ANSWER_MIN_MARGIN = 0.04
_MAX_OPTION_KEYWORDS = 6
_STOPWORDS = {
    "about",
    "affected",
    "cause",
    "closer",
    "description",
    "does",
    "issue",
    "machine",
    "more",
    "observed",
    "not",
    "problem",
    "properly",
    "related",
    "robot",
    "something",
    "that",
    "the",
    "there",
    "this",
    "what",
    "which",
    "with",
    "work",
    "works",
}


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) >= 3 and token not in _STOPWORDS
    }


def _normalize_phrase(text: str) -> str:
    phrase = " ".join((text or "").strip().split())
    return phrase.rstrip(" .")


def _title_case_component(component: str) -> str:
    value = _normalize_phrase(component)
    if not value:
        return ""
    if value.isupper():
        return value
    return value[0].upper() + value[1:]


def _observable_from_symptom(profile: dict[str, Any]) -> str:
    symptom_names = profile.get("symptom_names") or []
    if not symptom_names:
        return ""
    symptom = _normalize_phrase(str(symptom_names[0]))
    symptom_lower = symptom.lower()

    if symptom_lower.startswith("problem "):
        return symptom
    if symptom_lower.startswith("flexpendant"):
        return symptom
    if symptom_lower.startswith("robot "):
        return symptom
    if symptom_lower.startswith("controller "):
        return symptom
    if symptom_lower.startswith("the "):
        return symptom[0].upper() + symptom[1:]
    return symptom[0].upper() + symptom[1:]


def _observable_from_failure_mode(profile: dict[str, Any]) -> str:
    failure_mode = _normalize_phrase(str(profile.get("failure_mode_name", ""))).lower()
    component_names = profile.get("component_names") or []
    component = _title_case_component(component_names[0]) if component_names else ""
    component_lower = component.lower()

    if "deflected" in failure_mode or "stuck" in failure_mode:
        subject = component or "The control"
        return f"{subject} feels stuck, offset, or does not return to center"
    if "malfunction" in failure_mode:
        subject = component or "The control"
        return f"{subject} does not react correctly to input or movement"
    if "power supply" in failure_mode or "mains power supply loss" in failure_mode:
        subject = component or "The unit"
        return f"{subject} stays off, drops power, or restarts intermittently"
    if "switched off" in failure_mode:
        subject = component or "Main switches"
        return f"{subject} appear to be off"
    if "cable connector damaged" in failure_mode:
        return "The problem changes when the cable or connector is touched or moved"
    if "cable" in failure_mode and ("damaged" in failure_mode or "faulty" in failure_mode):
        return "The issue points to a damaged or unstable cable"
    if "not connected" in failure_mode or "connection missing" in failure_mode:
        if "drive" in failure_mode:
            return "The controller and drive modules do not seem to communicate correctly"
        return "A connection seems loose, missing, or not established"
    if "ethernet network" in failure_mode or "network" in failure_mode:
        return "Communication with the controller looks missing or unstable"
    if "update interval set too low" in failure_mode:
        return "The controller is slow or lagging rather than fully off"
    if "addresses the system too frequently" in failure_mode:
        return "An external system is overloading or spamming the controller"
    if "software" in failure_mode or "program" in failure_mode:
        return "The issue looks related to software start-up or execution"
    if "computer has problems" in failure_mode:
        return "The screen starts but the display image never appears correctly"

    if component_lower == "joystick":
        return "The joystick does not behave normally during jogging"
    if component:
        return f"The issue appears around the {component.lower()}"
    return ""


def _human_signal_label(signal: str, component_name: str) -> str:
    component = component_name.strip()
    component_lower = component.lower()
    if signal == "communication":
        if "controller" in component_lower or "control" in component_lower:
            return "communication/network with the controller"
        if "drive" in component_lower:
            return "communication between controller and drive modules"
        return "communication/network problem"
    if signal == "input":
        if "flexpendant" in component_lower:
            return "manual control or joystick on the FlexPendant"
        return "manual control or input problem"
    if signal == "display":
        if "flexpendant" in component_lower:
            return "screen or display on the FlexPendant"
        return "display or indicator problem"
    if signal == "power":
        return "power, fuse or mains supply problem"
    if signal == "software":
        return "software or program execution problem"
    if signal == "thermal":
        return "overheating or temperature problem"
    if signal == "mechanical":
        if component:
            return f"{component} mechanical or movement problem"
        return "mechanical movement or noise problem"
    return ""


def _fallback_option_label(profile: dict[str, Any]) -> str:
    component_names = profile.get("component_names") or []
    symptom_names = profile.get("symptom_names") or []
    component = component_names[0] if component_names else ""
    failure_mode_name = (profile.get("failure_mode_name") or "").strip()

    if component and failure_mode_name:
        return f"{component}: {failure_mode_name.lower()}"
    if component:
        return component
    if symptom_names:
        return symptom_names[0]
    return failure_mode_name or "this failure mode"


def _specific_option_label(profile: dict[str, Any]) -> str:
    component_names = profile.get("component_names") or []
    component = component_names[0] if component_names else ""
    failure_mode_name = (profile.get("failure_mode_name") or "").strip()
    if component and failure_mode_name:
        if component.lower() in failure_mode_name.lower():
            return failure_mode_name
        return f"{component}: {failure_mode_name.lower()}"
    return failure_mode_name or _fallback_option_label(profile)


def _build_option(group: dict[str, Any], option_id: str, index: OntologyIndex) -> dict[str, Any]:
    group_paths = group.get("paths", [])
    profile = describe_group_alignment(group_paths, index)
    component_names = profile.get("component_names") or []
    component_name = component_names[0] if component_names else ""
    signal_concepts = profile.get("signal_concepts") or []

    label = ""
    for signal in signal_concepts:
        label = _human_signal_label(signal, component_name)
        if label:
            break
    if not label:
        label = _fallback_option_label(profile)

    observable_symptom = _observable_from_symptom(profile)
    observable_failure_mode = _observable_from_failure_mode(profile)

    symptom_names = profile.get("symptom_names") or []
    detail_parts: list[str] = []
    if observable_symptom:
        detail_parts.append(f"Observed symptom: {observable_symptom}")
    if observable_failure_mode and observable_failure_mode.lower() != observable_symptom.lower():
        detail_parts.append(f"Distinguishing clue: {observable_failure_mode}")
    if component_name and component_name.lower() not in label.lower() and not observable_failure_mode:
        detail_parts.append(f"Component: {component_name}")
    if not detail_parts and profile.get("failure_mode_name"):
        detail_parts.append(f"Possible issue: {profile['failure_mode_name']}")

    keywords: list[str] = []
    for term in profile.get("direct_terms") or []:
        if len(term) >= 4 and term not in keywords:
            keywords.append(term)
        if len(keywords) >= _MAX_OPTION_KEYWORDS:
            break
    for term in profile.get("support_terms") or []:
        if len(term) >= 4 and term not in keywords:
            keywords.append(term)
        if len(keywords) >= _MAX_OPTION_KEYWORDS:
            break

    semantic_text = ". ".join(part for part in [label, *detail_parts] if part).strip()

    return {
        "id": option_id,
        "failure_mode_id": profile.get("failure_mode_id", ""),
        "label": label,
        "specific_label": _specific_option_label(profile),
        "observable_symptom": observable_symptom,
        "observable_failure_mode": observable_failure_mode,
        "description": " ".join(detail_parts).strip(),
        "keywords": keywords,
        "semantic_text": semantic_text,
        "signal_concepts": signal_concepts,
        "component_names": component_names,
        "failure_mode_name": profile.get("failure_mode_name", ""),
    }


def should_ask_clarification(
    ranked_groups: list[dict[str, Any]],
    index: OntologyIndex,
    user_message: str = "",
) -> bool:
    if len(ranked_groups) < 2:
        return False

    first = ranked_groups[0]
    second = ranked_groups[1]
    first_score = float(first.get("_query_rank_score") or 0.0)
    second_score = float(second.get("_query_rank_score") or 0.0)
    if first_score <= 0.0 or second_score <= 0.0:
        return False

    score_gap = first_score - second_score
    if score_gap > _ASK_CLARIFICATION_MAX_SCORE_GAP:
        return False
    if second_score / first_score < _ASK_CLARIFICATION_MIN_RATIO:
        return False

    first_profile = describe_group_alignment(first.get("paths", []), index)
    second_profile = describe_group_alignment(second.get("paths", []), index)
    query_terms = _tokenize(user_message)

    first_components = set(first_profile.get("component_names") or [])
    second_components = set(second_profile.get("component_names") or [])
    first_signals = set(first_profile.get("signal_concepts") or [])
    second_signals = set(second_profile.get("signal_concepts") or [])

    if first_signals and second_signals and first_signals != second_signals:
        return True
    if first_components and second_components and first_components != second_components:
        return True

    if len(query_terms) <= 3 and first_profile.get("failure_mode_name") != second_profile.get("failure_mode_name"):
        if first_components and first_components == second_components:
            shared_component_terms = _tokenize(" ".join(first_components))
            if query_terms & shared_component_terms:
                return False
        return True

    first_terms = set(first_profile.get("direct_canonical_terms") or [])
    second_terms = set(second_profile.get("direct_canonical_terms") or [])
    return bool(first_terms.symmetric_difference(second_terms))


def build_clarification_state(
    ranked_groups: list[dict[str, Any]],
    user_message: str,
    index: OntologyIndex,
) -> dict[str, Any] | None:
    if not should_ask_clarification(ranked_groups, index, user_message=user_message):
        return None

    options = [
        _build_option(ranked_groups[0], "1", index),
        _build_option(ranked_groups[1], "2", index),
    ]
    observable_symptoms = [str(option.get("observable_symptom", "")).strip() for option in options]
    observable_failures = [str(option.get("observable_failure_mode", "")).strip() for option in options]

    if observable_symptoms[0] and observable_symptoms[1] and observable_symptoms[0].lower() != observable_symptoms[1].lower():
        options[0]["label"] = observable_symptoms[0]
        options[1]["label"] = observable_symptoms[1]
    elif observable_failures[0] and observable_failures[1] and observable_failures[0].lower() != observable_failures[1].lower():
        options[0]["label"] = observable_failures[0]
        options[1]["label"] = observable_failures[1]

    first_labels = [_tokenize(options[0]["label"]), _tokenize(options[0]["specific_label"])]
    second_labels = [_tokenize(options[1]["label"]), _tokenize(options[1]["specific_label"])]
    shared_default_tokens = first_labels[0] & second_labels[0]
    default_labels_overlap = first_labels[0] == second_labels[0] or (
        first_labels[0] and second_labels[0] and len(first_labels[0] & second_labels[0]) >= min(len(first_labels[0]), len(second_labels[0]))
    )
    if default_labels_overlap or len(shared_default_tokens) >= 2:
        options[0]["label"] = options[0]["specific_label"]
        options[1]["label"] = options[1]["specific_label"]
        for option in options:
            option["semantic_text"] = ". ".join(
                part for part in [option.get("label", ""), option.get("description", "")]
                if part
            ).strip()

    labels = [option["label"].strip().lower() for option in options]
    if not labels[0] or not labels[1] or labels[0] == labels[1]:
        return None

    question = (
        "I found two plausible troubleshooting directions and I need one quick clarification before I suggest a cause.\n\n"
        "Which of these is closer to what you actually observe?\n"
        f"1. **{options[0]['label']}**\n"
        f"2. **{options[1]['label']}**\n\n"
        "Reply with **1** or **2**, or answer in a few words using the same meaning."
    )

    return {
        "original_message": user_message,
        "question": question,
        "options": options,
        "attempts": 0,
    }


def _find_option_by_failure_mode(ranked_groups: list[dict[str, Any]], failure_mode_id: str) -> dict[str, Any] | None:
    for group in ranked_groups:
        if group.get("failure_mode_id") == failure_mode_id:
            return group
    return None


def reorder_ranked_groups(
    ranked_groups: list[dict[str, Any]],
    clarification_state: dict[str, Any],
    selected_option_id: str,
) -> list[dict[str, Any]]:
    selected_option = next(
        (option for option in clarification_state.get("options", []) if option.get("id") == selected_option_id),
        None,
    )
    if not selected_option:
        return ranked_groups

    selected_failure_mode_id = selected_option.get("failure_mode_id", "")
    if not selected_failure_mode_id:
        return ranked_groups

    selected_group = _find_option_by_failure_mode(ranked_groups, selected_failure_mode_id)
    if not selected_group:
        return ranked_groups

    reordered = [selected_group]
    reordered.extend(
        group
        for group in ranked_groups
        if group.get("failure_mode_id") != selected_failure_mode_id
    )
    return reordered


def resolve_clarification_answer(
    answer: str,
    clarification_state: dict[str, Any],
) -> dict[str, Any]:
    normalized_answer = (answer or "").strip().lower()
    if not normalized_answer:
        return {"status": "invalid"}

    options = clarification_state.get("options", [])
    if len(options) < 2:
        return {"status": "invalid"}

    # Direct opt-out match: if the operator says "none of these" (or similar),
    # pick the opt-out option even when the other options share tokens.
    opt_out_option = next(
        (option for option in options if option.get("is_opt_out")),
        None,
    )
    if opt_out_option is not None and any(phrase in normalized_answer for phrase in _OPT_OUT_PHRASES):
        return {"status": "selected", "option_id": opt_out_option["id"]}

    for index, patterns in _ORDINAL_PATTERNS.items():
        if index >= len(options):
            break
        if any(re.search(pattern, normalized_answer) for pattern in patterns):
            return {"status": "selected", "option_id": options[index]["id"]}

    answer_terms = _tokenize(normalized_answer)
    lexical_scores: list[int] = []
    for option in options:
        option_terms = _tokenize(option.get("label", ""))
        option_terms.update(_tokenize(option.get("description", "")))
        option_terms.update(_tokenize(" ".join(option.get("keywords", []))))
        lexical_scores.append(len(answer_terms & option_terms))

    best_lexical = max(lexical_scores) if lexical_scores else 0
    if best_lexical > 0 and lexical_scores.count(best_lexical) == 1:
        return {
            "status": "selected",
            "option_id": options[lexical_scores.index(best_lexical)]["id"],
        }

    semantic_texts = [normalized_answer]
    semantic_texts.extend(option.get("semantic_text", "") for option in options)
    try:
        embeddings = get_text_embeddings(semantic_texts)
    except Exception:
        embeddings = []
    if len(embeddings) == 3:
        answer_embedding = embeddings[0]
        semantic_scores = [
            cosine_similarity(answer_embedding, option_embedding)
            for option_embedding in embeddings[1:]
        ]
        best_score = max(semantic_scores)
        second_score = min(semantic_scores)
        best_index = semantic_scores.index(best_score)
        if best_score >= _ANSWER_MIN_SIMILARITY and (best_score - second_score) >= _ANSWER_MIN_MARGIN:
            return {
                "status": "selected",
                "option_id": options[best_index]["id"],
            }

    return {"status": "invalid"}


def invalid_clarification_reply(clarification_state: dict[str, Any]) -> str:
    question = clarification_state.get("question", "Please clarify the issue.")
    return (
        "That answer does not seem to clarify the troubleshooting issue.\n\n"
        f"{question}"
    )
