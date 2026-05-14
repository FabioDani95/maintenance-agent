from __future__ import annotations

from typing import Literal


BehaviorMode = Literal["solve_current_problem", "search_past_events"]

SOLVE_CURRENT_PROBLEM: BehaviorMode = "solve_current_problem"
SEARCH_PAST_EVENTS: BehaviorMode = "search_past_events"

_SOLVE_INTENTS = {
    "troubleshooting_current",
    "hybrid_diagnosis_with_history",
}
_SEARCH_INTENTS = {
    "log_history_search",
    "log_analytics",
    "work_order_lookup",
}


def behavior_mode_for_intent(intent: str | None) -> BehaviorMode:
    """Collapse detailed chat intents into the two product behaviours.

    The chatbot has two user-facing jobs:
    1. solve a current troubleshooting problem;
    2. search or analyse past machine events.

    Detailed intents remain useful implementation details, but the rest of the
    workflow should make product decisions from this coarser mode.
    """
    if intent in _SEARCH_INTENTS:
        return SEARCH_PAST_EVENTS
    if intent in _SOLVE_INTENTS:
        return SOLVE_CURRENT_PROBLEM
    return SOLVE_CURRENT_PROBLEM


def is_search_mode(intent: str | None) -> bool:
    return behavior_mode_for_intent(intent) == SEARCH_PAST_EVENTS

