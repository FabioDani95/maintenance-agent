from __future__ import annotations

from typing import Any

from .ontology_loader import OntologyIndex, get_index


def get_troubleshooting_paths_from_error_code(
    error_code_id: str,
    index: OntologyIndex | None = None,
) -> list[dict[str, Any]]:
    active_index = index or get_index()

    error_node = active_index.nodes_by_id.get(error_code_id, {})
    failure_modes = active_index.indicates.get(error_code_id, [])

    seen: set[tuple[str, str]] = set()
    paths: list[dict[str, Any]] = []

    for fm_id in failure_modes:
        fm_node = active_index.nodes_by_id.get(fm_id, {})
        actions = active_index.resolved_by.get(fm_id, [])

        component_ids = active_index.affects.get(fm_id, [])
        comp_node = active_index.nodes_by_id.get(component_ids[0], {}) if component_ids else {}

        for act_id in actions:
            key = (fm_id, act_id)
            if key in seen:
                continue
            seen.add(key)

            act_node = active_index.nodes_by_id.get(act_id, {})
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
    active_index = index or get_index()

    normalized = code_value.upper().replace("_", "-")
    for ec in active_index.error_codes:
        stored = ec.get("code", "").upper().replace("_", "-")
        if stored == normalized:
            return ec.get("error_code_id")
    return None


def get_troubleshooting_paths(
    symptom_ids: list[str],
    index: OntologyIndex | None = None,
) -> list[dict[str, Any]]:
    active_index = index or get_index()

    seen: set[tuple[str, str]] = set()
    paths: list[dict[str, Any]] = []

    for sym_id in symptom_ids:
        symptom_node = active_index.nodes_by_id.get(sym_id, {})
        failure_modes = active_index.may_indicate.get(sym_id, [])

        for fm_id in failure_modes:
            fm_node = active_index.nodes_by_id.get(fm_id, {})
            actions = active_index.resolved_by.get(fm_id, [])

            component_ids = active_index.affects.get(fm_id, [])
            comp_node = active_index.nodes_by_id.get(component_ids[0], {}) if component_ids else {}

            for act_id in actions:
                key = (fm_id, act_id)
                if key in seen:
                    continue
                seen.add(key)

                act_node = active_index.nodes_by_id.get(act_id, {})
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


def get_troubleshooting_paths_from_failure_modes(
    failure_mode_ids: list[str],
    index: OntologyIndex | None = None,
) -> list[dict[str, Any]]:
    """Build paths starting from failure modes matched directly by the query.

    A representative symptom from ``may_indicate_inverse`` is attached when one
    exists, so downstream rerank/clarification code (which keys on ``symptom_id``)
    keeps working. If no symptom points at the fm, the fm itself is used as
    a pseudo-symptom so the path is still well-formed.
    """
    active_index = index or get_index()

    seen: set[tuple[str, str]] = set()
    paths: list[dict[str, Any]] = []

    for fm_id in failure_mode_ids:
        fm_node = active_index.nodes_by_id.get(fm_id, {})
        if not fm_node:
            continue
        actions = active_index.resolved_by.get(fm_id, [])
        if not actions:
            continue

        component_ids = active_index.affects.get(fm_id, [])
        comp_node = active_index.nodes_by_id.get(component_ids[0], {}) if component_ids else {}

        linked_symptom_ids = active_index.may_indicate_inverse.get(fm_id, [])
        if linked_symptom_ids:
            rep_symptom_id = linked_symptom_ids[0]
            rep_symptom_node = active_index.nodes_by_id.get(rep_symptom_id, {})
            rep_symptom_name = rep_symptom_node.get("name", rep_symptom_id)
        else:
            rep_symptom_id = fm_id
            rep_symptom_name = fm_node.get("name", fm_id)

        for act_id in actions:
            key = (fm_id, act_id)
            if key in seen:
                continue
            seen.add(key)

            act_node = active_index.nodes_by_id.get(act_id, {})
            paths.append({
                "symptom_id": rep_symptom_id,
                "symptom_name": rep_symptom_name,
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
