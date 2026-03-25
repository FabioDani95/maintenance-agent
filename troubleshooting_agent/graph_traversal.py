from __future__ import annotations

from typing import Any

from ontology_loader import OntologyIndex, get_index


def get_troubleshooting_paths_from_error_code(
    error_code_id: str,
    index: OntologyIndex | None = None,
) -> list[dict[str, Any]]:
    """
    Traverse: ErrorCode → FailureMode → CorrectiveAction.

    Returns the same path dict structure as get_troubleshooting_paths,
    with error_code_id and error_code_name populated instead of symptom fields.
    """
    if index is None:
        index = get_index()

    error_node = index.nodes_by_id.get(error_code_id, {})
    failure_modes = index.indicates.get(error_code_id, [])

    seen: set[tuple[str, str]] = set()
    paths: list[dict[str, Any]] = []

    for fm_id in failure_modes:
        fm_node = index.nodes_by_id.get(fm_id, {})
        actions = index.resolved_by.get(fm_id, [])

        component_ids = index.affects.get(fm_id, [])
        comp_node = index.nodes_by_id.get(component_ids[0], {}) if component_ids else {}

        for act_id in actions:
            key = (fm_id, act_id)
            if key in seen:
                continue
            seen.add(key)

            act_node = index.nodes_by_id.get(act_id, {})
            paths.append({
                "symptom_id": error_code_id,
                "symptom_name": f"Error {error_node.get('code', error_code_id)}: {error_node.get('name', error_code_id)}",
                "error_code_id": error_code_id,
                "error_code_name": error_node.get("name", error_code_id),
                "error_code": error_node.get("code", ""),
                "failure_mode_id": fm_id,
                "failure_mode_name": fm_node.get("name", fm_id),
                "component_id": comp_node.get("component_id", ""),
                "component_name": comp_node.get("name", ""),
                "action_id": act_id,
                "action_name": act_node.get("name", act_id),
                "instruction_text": act_node.get("instruction_text", ""),
                "source_title": act_node.get("source_title", ""),
                "source_reference": act_node.get("source_reference", ""),
                "related_measurements": fm_node.get("related_measurements", []),
            })

    return paths


def find_error_code_by_value(
    code_value: str,
    index: OntologyIndex | None = None,
) -> str | None:
    """
    Given a raw code string (e.g. '0300-0900-0002-0001' or '0300_0900_0002_0001'),
    return the matching error_code_id or None.
    """
    if index is None:
        index = get_index()

    normalized = code_value.upper().replace("_", "-")
    for ec in index.error_codes:
        stored = ec.get("code", "").upper().replace("_", "-")
        if stored == normalized:
            return ec.get("error_code_id")
    return None


def get_troubleshooting_paths(
    symptom_ids: list[str],
    index: OntologyIndex | None = None,
) -> list[dict[str, Any]]:
    """
    For each symptom_id, traverse: Symptom → FailureMode → CorrectiveAction.

    Returns a deduplicated list of path dicts, each containing:
      symptom_id, symptom_name,
      failure_mode_id, failure_mode_name,
      component_id, component_name,      (first affected component, if any)
      action_id, action_name,
      instruction_text,
      source_title, source_reference
    """
    if index is None:
        index = get_index()

    seen: set[tuple[str, str]] = set()  # (failure_mode_id, action_id)
    paths: list[dict[str, Any]] = []

    for sym_id in symptom_ids:
        symptom_node = index.nodes_by_id.get(sym_id, {})
        failure_modes = index.may_indicate.get(sym_id, [])

        for fm_id in failure_modes:
            fm_node = index.nodes_by_id.get(fm_id, {})
            actions = index.resolved_by.get(fm_id, [])

            # Component: first one affected by this failure mode
            component_ids = index.affects.get(fm_id, [])
            comp_node = index.nodes_by_id.get(component_ids[0], {}) if component_ids else {}

            for act_id in actions:
                key = (fm_id, act_id)
                if key in seen:
                    continue
                seen.add(key)

                act_node = index.nodes_by_id.get(act_id, {})
                paths.append({
                    "symptom_id": sym_id,
                    "symptom_name": symptom_node.get("name", sym_id),
                    "failure_mode_id": fm_id,
                    "failure_mode_name": fm_node.get("name", fm_id),
                    "component_id": comp_node.get("component_id", ""),
                    "component_name": comp_node.get("name", ""),
                    "action_id": act_id,
                    "action_name": act_node.get("name", act_id),
                    "instruction_text": act_node.get("instruction_text", ""),
                    "source_title": act_node.get("source_title", ""),
                    "source_reference": act_node.get("source_reference", ""),
                    "related_measurements": fm_node.get("related_measurements", []),
                })

    return paths
