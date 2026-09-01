#!/usr/bin/env python3
"""
shadow_sandbox/remediation/confidence_analyzer.py

Confidence Ratio Analyser for automated shadow sandboxing remediation actions.
Evaluates safety and historical success of proposed actions after static guardrails pass.
"""

import os
import json
from typing import Dict, Any, Optional

DEFAULT_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend_data",
    "chaos_history.json"
)


def _get_historical_success_rate(tool: str, history_path: str) -> float:
    """Reads historical success rate of a tool from history_path (or fallback 0.85)."""
    if not history_path or not os.path.exists(history_path):
        return 0.85

    try:
        with open(history_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return 0.85

    if isinstance(data, dict):
        # Format A: {"tools": {"restart_container": {"success_rate": 0.9}}} or {"restart_container": 0.9}
        tools_dict = data.get("tools") if isinstance(data.get("tools"), dict) else data
        if tool in tools_dict:
            entry = tools_dict[tool]
            if isinstance(entry, dict) and "success_rate" in entry:
                try:
                    return float(entry["success_rate"])
                except (ValueError, TypeError):
                    pass
            elif isinstance(entry, (int, float)):
                return float(entry)

    elif isinstance(data, list):
        # Format B: List of chaos event / tool outcome objects
        matching = [
            e for e in data
            if isinstance(e, dict) and (
                e.get("tool") == tool or
                e.get("fault_name") == tool or
                e.get("name") == tool or
                e.get("action") == tool
            )
        ]
        if matching:
            # If explicit success_rate field is present in any entry
            for e in matching:
                if "success_rate" in e:
                    try:
                        return float(e["success_rate"])
                    except (ValueError, TypeError):
                        pass

            # Otherwise calculate from recovered/successful entries
            total = len(matching)
            successes = sum(
                1 for e in matching
                if e.get("status") in ("recovered", "success", "successful", "passed", "cleared", "executed")
                or e.get("success") is True
            )
            if total > 0:
                return successes / total

    return 0.85


def calculate_confidence(proposal: Dict[str, Any], history_path: Optional[str] = None) -> float:
    """
    Calculates confidence ratio between 0.0 and 1.0 for a remediation proposal.
    - Base score: 1.0
    - Target penalty: -0.15 if target contains 'postgres' or 'redis'
    - Tool penalty: -0.10 if tool is 'restart_container' or 'run_query'
    - Historical modifier: Multiply by tool success_rate from history_path (default 0.85)
    Returns rounded score (2 decimal places) clamped between 0.0 and 1.0.
    """
    if history_path is None:
        history_path = DEFAULT_HISTORY_PATH

    score = 1.0

    target = str(proposal.get("target") or "").lower()
    tool = str(proposal.get("tool") or "").lower()

    # Target Penalty (-0.15 if target contains 'postgres' or 'redis')
    if "postgres" in target or "redis" in target:
        score -= 0.15

    # Tool Penalty (-0.10 if tool is 'restart_container' or 'run_query')
    if tool in ("restart_container", "run_query"):
        score -= 0.10

    # Historical Modifier
    tool_raw = proposal.get("tool") or ""
    multiplier = _get_historical_success_rate(str(tool_raw), history_path)
    score *= multiplier

    # Clamp between 0.0 and 1.0 and round to 2 decimal places
    score = max(0.0, min(1.0, score))
    score = round(score, 2)
    score = max(0.0, min(1.0, score))

    return score


if __name__ == "__main__":
    test_proposal = {"target": "shadow-postgres-db", "tool": "run_query"}
    print(f"Confidence score for {test_proposal}: {calculate_confidence(test_proposal)}")
