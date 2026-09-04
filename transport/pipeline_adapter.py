"""Pipeline input adapter for normalizing incoming staged transport events for the Laptop2 pipeline."""

import copy
from typing import Any, Dict, Optional


def normalize_for_pipeline(problem: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalizes incoming problem/incident dictionaries for downstream pipeline execution
    without mutating the original input or file.

    Key Normalizations:
    - Deep-copies input to guarantee immutability of the source object.
    - Preserves all 6 canonical transport blocks intact.
    - Normalizes `service_health_status.dependency_states` values:
      * dict: preserved as-is.
      * str (e.g. "healthy"): converted to {"status": str_val, "health": str_val}.
      * None / other non-dict: converted to {"status": "unknown", "health": "unknown"}.
    - Preserves all other fields (incident_id, telemetry, topology, chaos context).
    """
    if not isinstance(problem, dict):
        raise TypeError(f"Expected problem to be a dict, got {type(problem).__name__}")

    normalized = copy.deepcopy(problem)

    health_status = normalized.get("service_health_status")
    if isinstance(health_status, dict):
        dep_states = health_status.get("dependency_states")
        if isinstance(dep_states, dict):
            normalized_deps = {}
            for dep_name, state_val in dep_states.items():
                if isinstance(state_val, dict):
                    normalized_deps[dep_name] = state_val
                elif isinstance(state_val, str):
                    normalized_deps[dep_name] = {
                        "status": state_val,
                        "health": state_val
                    }
                else:
                    normalized_deps[dep_name] = {
                        "status": "unknown",
                        "health": "unknown"
                    }
            health_status["dependency_states"] = normalized_deps

    return normalized
