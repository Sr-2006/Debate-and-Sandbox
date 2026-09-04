"""Tests for transport to pipeline input adapter normalization."""

import copy
import json
from pathlib import Path
import pytest

from transport.pipeline_adapter import normalize_for_pipeline
from transport.contracts import CANONICAL_BLOCKS
from debate.incident_parser import IncidentParser


def test_string_dependency_state_normalization():
    """A. String dependency state 'healthy' is normalized to status/health dict."""
    problem = {
        "service_health_status": {
            "docker_status": "running",
            "health_check": "healthy",
            "dependency_states": {
                "postgres-db": "healthy"
            }
        }
    }
    normalized = normalize_for_pipeline(problem)
    dep_states = normalized["service_health_status"]["dependency_states"]
    assert dep_states["postgres-db"] == {
        "status": "healthy",
        "health": "healthy"
    }


def test_dict_dependency_state_preserved():
    """B. Dict dependency state is preserved unchanged."""
    original_dep = {
        "status": "degraded",
        "health": "unhealthy",
        "latency_p99": 450
    }
    problem = {
        "service_health_status": {
            "dependency_states": {
                "redis-cache": original_dep
            }
        }
    }
    normalized = normalize_for_pipeline(problem)
    assert normalized["service_health_status"]["dependency_states"]["redis-cache"] == original_dep


def test_multiple_dependencies_mixed():
    """C. Multiple dependencies with mixed string and dict states."""
    problem = {
        "service_health_status": {
            "dependency_states": {
                "postgres-db": "healthy",
                "redis-cache": {"status": "ok", "health": "healthy"},
                "payment-gateway": "degraded"
            }
        }
    }
    normalized = normalize_for_pipeline(problem)
    deps = normalized["service_health_status"]["dependency_states"]
    assert deps["postgres-db"] == {"status": "healthy", "health": "healthy"}
    assert deps["redis-cache"] == {"status": "ok", "health": "healthy"}
    assert deps["payment-gateway"] == {"status": "degraded", "health": "degraded"}


def test_input_immutability():
    """D. Source input dictionary is not mutated in-place."""
    problem = {
        "service_health_status": {
            "dependency_states": {
                "postgres-db": "healthy"
            }
        }
    }
    problem_copy = copy.deepcopy(problem)
    normalized = normalize_for_pipeline(problem)

    # Original must remain completely unchanged
    assert problem == problem_copy
    assert problem["service_health_status"]["dependency_states"]["postgres-db"] == "healthy"
    assert normalized["service_health_status"]["dependency_states"]["postgres-db"] == {
        "status": "healthy",
        "health": "healthy"
    }


def test_unknown_or_null_state_conservative():
    """E. Unknown, null, or non-dict/non-string values handled conservatively."""
    problem = {
        "service_health_status": {
            "dependency_states": {
                "dep_none": None,
                "dep_int": 123,
                "dep_list": ["invalid"]
            }
        }
    }
    normalized = normalize_for_pipeline(problem)
    deps = normalized["service_health_status"]["dependency_states"]
    assert deps["dep_none"] == {"status": "unknown", "health": "unknown"}
    assert deps["dep_int"] == {"status": "unknown", "health": "unknown"}
    assert deps["dep_list"] == {"status": "unknown", "health": "unknown"}


def test_exact_six_canonical_blocks_preserved():
    """F. Exact six canonical blocks preserved without modifying metadata."""
    problem = {
        "system_context": {"env": "staging"},
        "incident_event": {"incident_id": "order-service_51", "target_service": "order-service"},
        "infrastructure_topology": {"role": "backend-api"},
        "service_health_status": {
            "docker_status": "running",
            "dependency_states": {"postgres-db": "healthy"}
        },
        "telemetry_evidence": {"log_samples": [{"timestamp": "2026-09-04", "level": "ERROR", "content": "timeout"}]},
        "injected_chaos_context": {"active_infrastructure_mutations": "latency_injection"}
    }
    normalized = normalize_for_pipeline(problem)
    for block in CANONICAL_BLOCKS:
        assert block in normalized
    assert normalized["incident_event"]["incident_id"] == "order-service_51"
    assert normalized["telemetry_evidence"]["log_samples"][0]["content"] == "timeout"
    assert normalized["injected_chaos_context"]["active_infrastructure_mutations"] == "latency_injection"


def test_staged_order_service_51_incident_parser_pass():
    """G. order-service_51 staged file passes IncidentParser.parse_and_format without AttributeError."""
    staged_path = Path("runtime/transport_inputs/evt_70cbb2cc70b04630ae49698d7fac19f7.json")
    if not staged_path.exists():
        pytest.skip(f"Staged file {staged_path} not found")

    with open(staged_path, "r", encoding="utf-8") as f:
        staged_content = f.read()
        raw_problem = json.loads(staged_content)

    # 1. Normalize
    normalized = normalize_for_pipeline(raw_problem)

    # 2. Verify file on disk was NOT mutated
    with open(staged_path, "r", encoding="utf-8") as f:
        assert f.read() == staged_content

    # 3. IncidentParser must parse without raising AttributeError or any exception
    formatted_text, inc_id = IncidentParser.parse_and_format(normalized)
    assert inc_id == "order-service_51"
    assert "INCIDENT CONTEXT [order-service_51]" in formatted_text
    assert "postgres-db" in formatted_text
    assert "otel-collector" in formatted_text
