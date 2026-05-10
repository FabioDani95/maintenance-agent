"""Generate the seed machine_logs.csv for the IRC5 instance.

Reads `log_seed_plan.json`, anchors content to the IRC5 ontology, and produces
~80-120 realistic log rows by calling the LLM once per scenario. Determinism on
structural fields (dates, severities, statuses, durations) is handled here; the
LLM only fills narrative fields (title, body, action_taken, semantic_text,
component_name_raw, error_code, alarm_code).

Usage:
    python -m kg_agents.scripts.generate_irc5_logs           # writes if missing
    python -m kg_agents.scripts.generate_irc5_logs --force   # overwrites
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from kg_agents.config import DATA_DIR, OPENAI_API_KEY, OPENAI_CHAT_MODEL

CSV_COLUMNS = [
    "log_id", "source_system", "source_record_id", "occurred_at", "observed_at",
    "instance_id", "asset_id", "device_id", "equipment_tag", "location",
    "event_name", "event_category", "maintenance_type", "status",
    "severity_number", "severity_text",
    "component_id", "component_name_raw", "error_code", "alarm_code",
    "signal_name", "observed_value", "observed_unit",
    "threshold_value", "threshold_unit",
    "work_order_id", "title", "body", "action_taken", "outcome",
    "planned_duration_min", "actual_duration_min", "downtime_min",
    "semantic_text", "event_signature_id",
    "linked_failure_mode_id", "linked_symptom_id",
    "quality_flags", "attributes_json",
]

SEVERITY_NUMBER = {"INFO": 10, "WARN": 14, "ERROR": 18, "FATAL": 22}

INSTANCE_DIR = DATA_DIR / "instances" / "irc5-default-instance"
PLAN_PATH = Path(__file__).parent / "log_seed_plan.json"
ONTOLOGY_PATH = INSTANCE_DIR / "ontology.json"
OUTPUT_CSV = INSTANCE_DIR / "logs" / "machine_logs.csv"


def load_ontology_index() -> dict[str, Any]:
    with ONTOLOGY_PATH.open("r", encoding="utf-8") as f:
        ontology = json.load(f)
    nodes = ontology["nodes"]
    rels = ontology["relationships"]

    fm_by_id = {fm["failure_mode_id"]: fm for fm in nodes.get("FailureMode", [])}
    comp_by_id = {c["component_id"]: c for c in nodes.get("Component", [])}
    sym_by_id = {s["symptom_id"]: s for s in nodes.get("Symptom", [])}
    ca_by_id = {a["action_id"]: a for a in nodes.get("CorrectiveAction", [])}

    # Build reverse index: failure_mode_id -> [symptom ids that may indicate it]
    fm_to_symptoms: dict[str, list[str]] = {}
    fm_to_actions: dict[str, list[str]] = {}
    for r in rels:
        if r.get("type") == "MAY_INDICATE":
            fm_to_symptoms.setdefault(r["to_id"], []).append(r["from_id"])
        elif r.get("type") == "RESOLVED_BY":
            fm_to_actions.setdefault(r["from_id"], []).append(r["to_id"])

    return {
        "fm_by_id": fm_by_id,
        "comp_by_id": comp_by_id,
        "sym_by_id": sym_by_id,
        "ca_by_id": ca_by_id,
        "fm_to_symptoms": fm_to_symptoms,
        "fm_to_actions": fm_to_actions,
    }


def build_scenario_context(scenario: dict[str, Any], idx: dict[str, Any]) -> dict[str, Any]:
    """Build the ontology context the LLM uses to ground each scenario."""
    fm_id = scenario.get("linked_failure_mode_id")
    comp_id = scenario.get("component_id")

    fm = idx["fm_by_id"].get(fm_id) if fm_id else None
    comp = idx["comp_by_id"].get(comp_id) if comp_id else None

    related_symptoms: list[dict[str, str]] = []
    related_actions: list[dict[str, str]] = []
    if fm_id:
        for sid in idx["fm_to_symptoms"].get(fm_id, [])[:5]:
            s = idx["sym_by_id"].get(sid)
            if s:
                related_symptoms.append({
                    "symptom_id": s.get("symptom_id", ""),
                    "name": s.get("name", ""),
                    "description": s.get("description", ""),
                })
        for aid in idx["fm_to_actions"].get(fm_id, [])[:6]:
            a = idx["ca_by_id"].get(aid)
            if a:
                related_actions.append({
                    "name": a.get("name", ""),
                    "instruction_text": a.get("instruction_text", ""),
                })

    return {
        "failure_mode": fm,
        "component": comp,
        "related_symptoms": related_symptoms,
        "related_actions": related_actions,
    }


def stable_seed_for_scenario(plan_seed: int, signature_id: str) -> int:
    h = hashlib.sha256(f"{plan_seed}:{signature_id}".encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def pick_dates(rng: random.Random, n: int, today: datetime, days_min: int, days_max: int) -> list[datetime]:
    """Distribute n events across [today-days_max, today-days_min] with jitter."""
    if n <= 0:
        return []
    span = max(days_max - days_min, 1)
    step = span / n
    dates: list[datetime] = []
    for i in range(n):
        center = days_max - (i + 0.5) * step
        jitter = rng.uniform(-step * 0.4, step * 0.4)
        days_ago = max(days_min, min(days_max, center + jitter))
        hour = rng.randint(6, 21)
        minute = rng.randint(0, 59)
        d = today - timedelta(days=days_ago, hours=-hour, minutes=-minute)
        dates.append(d.replace(microsecond=0, tzinfo=timezone.utc).replace(hour=hour, minute=minute))
    dates.sort()
    return dates


def llm_generate_narrative(
    scenario: dict[str, Any],
    context: dict[str, Any],
    occurrences: list[dict[str, Any]],
    client: OpenAI,
) -> list[dict[str, str]]:
    """Ask the LLM to produce narrative fields for each occurrence.

    Returns a list of dicts (same length as occurrences) with keys:
    title, body, action_taken, semantic_text, component_name_raw,
    error_code, alarm_code.
    """
    system = (
        "You are a senior maintenance engineer writing realistic CMMS work-order "
        "entries for an ABB IRC5 industrial robot controller. Your entries must "
        "sound like real technician notes: specific, varied across occurrences, "
        "occasionally terse, occasionally verbose, with concrete component names, "
        "axis numbers, voltages, temperatures, and observed findings. "
        "Avoid corporate boilerplate. Avoid identical phrasing across occurrences. "
        "Return your answer as a single JSON object with key 'occurrences' whose "
        "value is an array of one object per input occurrence, in the same order."
    )

    fm = context.get("failure_mode")
    comp = context.get("component")

    user_payload = {
        "scenario": {
            "event_signature_id": scenario["event_signature_id"],
            "event_name": scenario["event_name"],
            "event_category": scenario["event_category"],
            "maintenance_type": scenario.get("maintenance_type", ""),
            "narrative_hint": scenario["narrative_hint"],
        },
        "ontology_context": {
            "failure_mode": fm,
            "component": comp,
            "related_symptoms": context.get("related_symptoms", []),
            "related_actions": context.get("related_actions", []),
        },
        "occurrences": [
            {
                "index": i,
                "occurred_at": o["occurred_at"],
                "severity_text": o["severity_text"],
                "status": o["status"],
                "outcome": o["outcome"],
                "has_measurement": scenario.get("has_measurement", False),
                "signal_name": scenario.get("signal_name"),
                "observed_value": o.get("observed_value"),
                "observed_unit": scenario.get("observed_unit"),
                "threshold_value": scenario.get("threshold_value"),
                "threshold_unit": scenario.get("threshold_unit"),
                "actual_duration_min": o["actual_duration_min"],
                "downtime_min": o["downtime_min"],
            }
            for i, o in enumerate(occurrences)
        ],
        "instructions": (
            "For each occurrence return: title (max 80 chars), body "
            "(2-4 sentences describing what happened, with specific findings), "
            "action_taken (concrete steps the technician performed; for "
            "operator_note category and INFO severity with empty outcome, this "
            "may be empty or just 'noted, continued production'), "
            "component_name_raw (the raw component label as a technician would "
            "write it; may differ slightly from the ontology canonical name), "
            "error_code (digit-string IRC5 event code if appropriate, else ''), "
            "alarm_code (alarm code string if appropriate, else ''), "
            "semantic_text (a single line of dense technical keywords + a short "
            "phrase summarizing context, optimized for embedding-based retrieval; "
            "include asset 'IRC5', component name, key technical terms, codes, "
            "measurement name and abnormal value if present, and a fragment of "
            "the action). Each occurrence MUST have noticeably different phrasing "
            "from the others. Do not invent failure modes that contradict the "
            "narrative_hint. Vary occurrences as the hint suggests."
        ),
    }

    response = client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError(f"empty LLM response for {scenario['event_signature_id']}")
    parsed = json.loads(content)

    if isinstance(parsed, dict):
        if "occurrences" in parsed:
            items = parsed["occurrences"]
        elif "results" in parsed:
            items = parsed["results"]
        elif "items" in parsed:
            items = parsed["items"]
        else:
            # unwrap if there's exactly one list-valued key
            list_vals = [v for v in parsed.values() if isinstance(v, list)]
            if len(list_vals) == 1:
                items = list_vals[0]
            else:
                raise RuntimeError(f"unexpected LLM response shape: {list(parsed.keys())}")
    elif isinstance(parsed, list):
        items = parsed
    else:
        raise RuntimeError(f"unexpected LLM response type: {type(parsed)}")

    if len(items) != len(occurrences):
        raise RuntimeError(
            f"LLM returned {len(items)} occurrences, expected {len(occurrences)} "
            f"for {scenario['event_signature_id']}"
        )

    return [
        {
            "title": str(item.get("title", "")).strip(),
            "body": str(item.get("body", "")).strip(),
            "action_taken": str(item.get("action_taken", "")).strip(),
            "component_name_raw": str(item.get("component_name_raw", "")).strip(),
            "error_code": str(item.get("error_code", "")).strip(),
            "alarm_code": str(item.get("alarm_code", "")).strip(),
            "semantic_text": str(item.get("semantic_text", "")).strip(),
        }
        for item in items
    ]


def build_rows_for_scenario(
    scenario: dict[str, Any],
    context: dict[str, Any],
    plan: dict[str, Any],
    rng: random.Random,
    client: OpenAI,
    log_id_counter: list[int],
    wo_counter: list[int],
) -> list[dict[str, Any]]:
    n = scenario["n_occurrences"]
    today = datetime.fromisoformat(plan["today"]).replace(tzinfo=timezone.utc)
    days_max, days_min = scenario["date_range_days_ago"][0], scenario["date_range_days_ago"][1]

    dates = pick_dates(rng, n, today, days_min, days_max)

    severities = scenario["severity_text_distribution"][:n]
    statuses = scenario["status_distribution"][:n]
    outcomes = scenario["outcome_distribution"][:n]

    plan_lo, plan_hi = scenario["duration_planned_range"]
    act_lo, act_hi = scenario["duration_actual_range"]
    dt_lo, dt_hi = scenario["downtime_range"]

    occurrences: list[dict[str, Any]] = []
    for i in range(n):
        occ: dict[str, Any] = {
            "occurred_at": dates[i].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "severity_text": severities[i],
            "status": statuses[i],
            "outcome": outcomes[i],
            "planned_duration_min": rng.randint(plan_lo, plan_hi) if plan_hi > 0 else 0,
            "actual_duration_min": rng.randint(act_lo, act_hi) if act_hi > 0 else 0,
            "downtime_min": rng.randint(dt_lo, dt_hi) if dt_hi > 0 else 0,
        }
        if scenario.get("has_measurement"):
            lo, hi = scenario["observed_value_range"]
            if isinstance(lo, int) and isinstance(hi, int):
                occ["observed_value"] = lo if lo == hi else rng.randint(lo, hi)
            else:
                occ["observed_value"] = round(rng.uniform(float(lo), float(hi)), 2)
        occurrences.append(occ)

    print(f"  -> LLM generation for {scenario['event_signature_id']} ({n} occurrences)")
    narratives = llm_generate_narrative(scenario, context, occurrences, client)

    rows: list[dict[str, Any]] = []
    for occ, nar in zip(occurrences, narratives):
        log_id_counter[0] += 1
        wo_counter[0] += 1
        log_id = f"log_irc5_{log_id_counter[0]:04d}"
        wo_id = f"WO-IRC5-{1000 + wo_counter[0]}"

        observed_at = (
            datetime.strptime(occ["occurred_at"], "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc) + timedelta(minutes=rng.randint(0, 4))
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        # error_code from pool if scenario provides one
        error_code = nar.get("error_code") or ""
        if scenario.get("error_code_pool") and not error_code:
            error_code = rng.choice(scenario["error_code_pool"])

        attrs = {"raw_equipment_id": plan["equipment_tag"]}
        if scenario.get("error_code_pool") and error_code:
            attrs["error_code_source"] = "controller_event_log"

        row = {
            "log_id": log_id,
            "source_system": "mock_cmms",
            "source_record_id": wo_id,
            "occurred_at": occ["occurred_at"],
            "observed_at": observed_at,
            "instance_id": plan["instance_id"],
            "asset_id": plan["asset_id"],
            "device_id": plan["device_id"],
            "equipment_tag": plan["equipment_tag"],
            "location": plan["location"],
            "event_name": scenario["event_name"],
            "event_category": scenario["event_category"],
            "maintenance_type": scenario.get("maintenance_type", ""),
            "status": occ["status"],
            "severity_number": SEVERITY_NUMBER.get(occ["severity_text"], 10),
            "severity_text": occ["severity_text"],
            "component_id": scenario.get("component_id") or "",
            "component_name_raw": nar.get("component_name_raw") or scenario.get("component_name_raw", ""),
            "error_code": error_code,
            "alarm_code": nar.get("alarm_code", ""),
            "signal_name": scenario.get("signal_name", "") or "",
            "observed_value": occ.get("observed_value", "") if "observed_value" in occ else "",
            "observed_unit": scenario.get("observed_unit", "") if scenario.get("has_measurement") else "",
            "threshold_value": scenario.get("threshold_value", "") if scenario.get("has_measurement") else "",
            "threshold_unit": scenario.get("threshold_unit", "") if scenario.get("has_measurement") else "",
            "work_order_id": wo_id,
            "title": nar["title"],
            "body": nar["body"],
            "action_taken": nar["action_taken"],
            "outcome": occ["outcome"],
            "planned_duration_min": occ["planned_duration_min"] if occ["planned_duration_min"] > 0 else "",
            "actual_duration_min": occ["actual_duration_min"] if occ["actual_duration_min"] > 0 else "",
            "downtime_min": occ["downtime_min"] if occ["downtime_min"] > 0 else "",
            "semantic_text": nar["semantic_text"],
            "event_signature_id": scenario["event_signature_id"],
            "linked_failure_mode_id": scenario.get("linked_failure_mode_id") or "",
            "linked_symptom_id": "",  # filled by verify_log_links.py
            "quality_flags": "",
            "attributes_json": json.dumps(attrs, ensure_ascii=False),
        }
        rows.append(row)
    return rows


def inject_quality_flags(rows: list[dict[str, Any]], rng: random.Random, rate: float) -> None:
    """Mutate a small fraction of rows to simulate dirty source data."""
    n_to_flag = max(1, int(len(rows) * rate))
    indices = rng.sample(range(len(rows)), n_to_flag)
    for i in indices:
        flag_type = rng.choice([
            "missing_component",
            "date_imputed",
            "duration_outlier",
            "unmapped_failure_mode",
        ])
        if flag_type == "missing_component" and rows[i]["component_id"]:
            rows[i]["component_id"] = ""
            rows[i]["quality_flags"] = "missing_component"
        elif flag_type == "date_imputed":
            rows[i]["quality_flags"] = "date_imputed"
        elif flag_type == "duration_outlier" and rows[i]["actual_duration_min"]:
            rows[i]["actual_duration_min"] = int(rows[i]["actual_duration_min"]) * 5
            rows[i]["quality_flags"] = "duration_outlier"
        elif flag_type == "unmapped_failure_mode" and rows[i]["linked_failure_mode_id"]:
            rows[i]["linked_failure_mode_id"] = ""
            rows[i]["quality_flags"] = "unmapped_failure_mode"


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CSV_COLUMNS})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Overwrite existing CSV.")
    parser.add_argument("--out", type=Path, default=OUTPUT_CSV)
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 2

    if args.out.exists() and not args.force:
        print(f"{args.out} already exists. Use --force to overwrite.")
        return 0

    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    idx = load_ontology_index()

    client = OpenAI(api_key=OPENAI_API_KEY)
    base_seed = plan.get("seed", 42)

    all_rows: list[dict[str, Any]] = []
    log_id_counter = [0]
    wo_counter = [0]

    print(f"Generating logs for {len(plan['scenarios'])} scenarios...")
    for scenario in plan["scenarios"]:
        sid = scenario["event_signature_id"]
        rng = random.Random(stable_seed_for_scenario(base_seed, sid))
        context = build_scenario_context(scenario, idx)
        try:
            rows = build_rows_for_scenario(
                scenario, context, plan, rng, client, log_id_counter, wo_counter
            )
        except Exception as e:
            print(f"  FAILED: {sid}: {e}", file=sys.stderr)
            raise
        all_rows.extend(rows)

    rng_global = random.Random(base_seed)
    inject_quality_flags(all_rows, rng_global, plan.get("quality_flag_injection_rate", 0.08))

    # sort by occurred_at for readability
    all_rows.sort(key=lambda r: r["occurred_at"])

    write_csv(all_rows, args.out)
    print(f"Wrote {len(all_rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
