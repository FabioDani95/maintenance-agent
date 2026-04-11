from __future__ import annotations

from collections import OrderedDict
from typing import Any

from .telemetry_loader import get_signals, get_stats


def group_paths_by_failure_mode(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for path in paths:
        fm_id = path["failure_mode_id"]
        groups.setdefault(fm_id, []).append(path)
    return [{"failure_mode_id": fm_id, "paths": group_paths} for fm_id, group_paths in groups.items()]


def group_paths_by_symptom_score(
    paths: list[dict[str, Any]],
    top_symptoms: list[tuple[str, float]],
) -> list[dict[str, Any]]:
    score_map = {sid: score for sid, score in top_symptoms}
    grouped = group_paths_by_failure_mode(paths)
    for group in grouped:
        group["_best_score"] = max(
            score_map.get(path["symptom_id"], 0.0) for path in group["paths"]
        )
    grouped.sort(key=lambda group: group["_best_score"], reverse=True)
    for group in grouped:
        del group["_best_score"]
    return grouped


def build_trace(
    paths: list[dict[str, Any]],
    top_symptoms: list[tuple[str, float]] | None = None,
) -> dict[str, Any]:
    error_code_ids = list({path["error_code_id"] for path in paths if path.get("error_code_id")})
    symptom_ids = list({path["symptom_id"] for path in paths if not path.get("error_code_id")})
    failure_mode_ids = list({path["failure_mode_id"] for path in paths})
    action_ids = list({path["action_id"] for path in paths})
    component_ids = list({path["component_id"] for path in paths if path.get("component_id")})

    edges: list[dict[str, str]] = []
    for path in paths:
        if path.get("error_code_id"):
            edges.append({"from": path["error_code_id"], "to": path["failure_mode_id"]})
        else:
            edges.append({"from": path["symptom_id"], "to": path["failure_mode_id"]})
        edges.append({"from": path["failure_mode_id"], "to": path["action_id"]})
        if path.get("component_id"):
            edges.append({"from": path["failure_mode_id"], "to": path["component_id"]})

    scores = {}
    if top_symptoms:
        scores = {sid: round(score, 3) for sid, score in top_symptoms}

    reasoning: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, str]] = set()
    for path in paths:
        key = (path["failure_mode_id"], path["action_id"])
        if key in seen_paths:
            continue
        seen_paths.add(key)
        entry: dict[str, Any] = {
            "symptom": path["symptom_name"],
            "failure_mode": path["failure_mode_name"],
            "action": path["action_name"],
        }
        if path.get("component_name"):
            entry["component"] = path["component_name"]
        if path.get("source_title"):
            entry["source"] = path["source_title"]
            if path.get("source_reference"):
                entry["source_reference"] = path["source_reference"]
        if scores.get(path["symptom_id"]):
            entry["score"] = scores[path["symptom_id"]]
        reasoning.append(entry)

    return {
        "symptom_ids": symptom_ids,
        "error_code_ids": error_code_ids,
        "failure_mode_ids": failure_mode_ids,
        "action_ids": action_ids,
        "component_ids": component_ids,
        "edges": edges,
        "scores": scores,
        "reasoning": reasoning,
    }


def build_telemetry_payload(
    paths: list[dict[str, Any]],
    telemetry_dir: Any = None,
    telemetry_path: Any = None,
) -> dict[str, Any] | None:
    all_measurements: list[str] = []
    for path in paths:
        for measurement in path.get("related_measurements", []):
            if measurement not in all_measurements:
                all_measurements.append(measurement)
    if not all_measurements:
        return None

    signals = get_signals(
        all_measurements,
        telemetry_dir=telemetry_dir,
        telemetry_path=telemetry_path,
    )
    stats = get_stats(
        all_measurements,
        telemetry_dir=telemetry_dir,
        telemetry_path=telemetry_path,
    )
    if not signals:
        return None

    return {
        "signals": signals,
        "stats": stats,
        "columns": [column for column in all_measurements if column in signals],
    }
