"""Tests for transport incident event receiver validation against canonical Stage A contracts."""

import copy
import pytest

from transport.contracts import TransportReasonCode
from transport.canonical_json import compute_payload_sha256
from transport.validation import validate_incident_event


@pytest.fixture
def valid_event():
    payload = {
        "system_context": {
            "objective": "Test RCA",
            "environment": "Kubernetes",
            "current_health_score": 50,
            "active_warnings": 2
        },
        "incident_event": {
            "incident_id": "case_test_01",
            "target_service": "user-service",
            "priority_score": 85.0,
            "severity": "HIGH",
            "occurrence_count": 10
        },
        "infrastructure_topology": {
            "role": "auth-api",
            "downstream_dependencies": ["redis-cache"],
            "exposed_ports": ["8080:8080"]
        },
        "service_health_status": {
            "docker_status": "running",
            "health_check": "failing"
        },
        "telemetry_evidence": {
            "log_cluster_template": "FATAL OutOfMemory",
            "log_samples": [{"timestamp": "2026-08-10T17:00:00Z", "content": "OOM"}],
            "metrics_snapshot": [{"timestamp": "2026-08-10T17:00:00Z", "cpu_percent": 80.0}]
        },
        "injected_chaos_context": {
            "active_infrastructure_mutations": "RAM exhausted"
        },
        "laptop1_rl_advisory": {
            "advisory_id": "adv_001",
            "recommendation": "OBSERVE_FIRST"
        },
        "lineage_metadata": {
            "origin": "laptop1_collector"
        },
        "phase1_summary": {
            "status": "DETECTED"
        },
        "phase2_summary": {
            "status": "TRIAGED"
        }
    }

    payload_hash = compute_payload_sha256(payload)

    return {
        "schema_version": "1.0.0",
        "event_type": "autosre.incident.ready.v1",
        "event_id": "evt_test_1001",
        "parent_event_id": None,
        "root_event_id": "evt_test_1001",
        "correlation_id": "corr_test_1001",
        "incident_id": "case_test_01",
        "phase": "STAGE_A",
        "component": "incident_engine",
        "status": "READY",
        "timestamp": "2026-09-04T00:00:00Z",
        "source": {
            "engine": "laptop1",
            "host": "laptop1",
            "version": "1.0.0",
            "dataset_version": None,
            "git_sha": None,
            "generated_at": None
        },
        "metrics": {},
        "integrity": {
            "payload_sha256": payload_hash,
            "signature": None,
            "commit_sha": None
        },
        "payload": payload
    }


def test_valid_canonical_event_passes(valid_event):
    """Verify that a canonical Stage A autosre.incident.ready.v1 event passes validation."""
    result = validate_incident_event(valid_event)
    assert result.is_valid is True
    assert result.reason_code == TransportReasonCode.VALID
    assert result.errors == []
    assert result.computed_hash == valid_event["integrity"]["payload_sha256"]


def test_live_style_event_shape_passes(valid_event):
    """Verify exact live-style shape with all Stage A blocks and envelope metadata is accepted."""
    event = copy.deepcopy(valid_event)
    event["event_id"] = "evt_000ae537e54847e3b00c307fa632b19f"
    event["correlation_id"] = "corr_251ba019509c4aef948753d1e218e364"
    event["incident_id"] = "order-service_51"
    event["payload"]["incident_event"]["incident_id"] = "order-service_51"
    event["integrity"]["payload_sha256"] = compute_payload_sha256(event["payload"])

    result = validate_incident_event(event)
    assert result.is_valid is True
    assert result.reason_code == TransportReasonCode.VALID


def test_legacy_event_type_rejected(valid_event):
    """Verify old pre-v1 autosre.incident.ready event type is strictly rejected."""
    event = copy.deepcopy(valid_event)
    event["event_type"] = "autosre.incident.ready"

    result = validate_incident_event(event)
    assert result.is_valid is False
    assert result.reason_code == TransportReasonCode.REJECTED_SCHEMA


def test_missing_canonical_block_fails(valid_event):
    """Verify that omitting any of the 6 canonical blocks triggers MISSING_CANONICAL_BLOCK."""
    event = copy.deepcopy(valid_event)
    del event["payload"]["injected_chaos_context"]
    event["integrity"]["payload_sha256"] = compute_payload_sha256(event["payload"])

    result = validate_incident_event(event)
    assert result.is_valid is False
    assert result.reason_code in (TransportReasonCode.MISSING_CANONICAL_BLOCK, TransportReasonCode.REJECTED_SCHEMA)


def test_incident_id_mismatch_fails(valid_event):
    """Verify top-level incident_id differing from payload.incident_event.incident_id fails."""
    event = copy.deepcopy(valid_event)
    event["incident_id"] = "mismatched_id_999"

    result = validate_incident_event(event)
    assert result.is_valid is False
    assert result.reason_code == TransportReasonCode.INCIDENT_ID_MISMATCH
    assert any("mismatched_id_999" in err for err in result.errors)


def test_invalid_payload_hash_fails(valid_event):
    """Verify corrupted or incorrect payload_sha256 triggers INVALID_PAYLOAD_HASH."""
    event = copy.deepcopy(valid_event)
    event["integrity"]["payload_sha256"] = "0" * 64

    result = validate_incident_event(event)
    assert result.is_valid is False
    assert result.reason_code == TransportReasonCode.INVALID_PAYLOAD_HASH
    assert any("Payload hash mismatch" in err for err in result.errors)


def test_invalid_source_engine_fails(valid_event):
    """Verify source.engine other than 'laptop1' fails."""
    event = copy.deepcopy(valid_event)
    event["source"]["engine"] = "unknown_laptop"

    result = validate_incident_event(event)
    assert result.is_valid is False
    assert result.reason_code in (TransportReasonCode.INVALID_SOURCE_ENGINE, TransportReasonCode.REJECTED_SCHEMA)


def test_unknown_extra_payload_field_rejected(valid_event):
    """Verify unexpected fields inside payload are strictly rejected (fail-closed)."""
    event = copy.deepcopy(valid_event)
    event["payload"]["unauthorized_payload_data"] = {"malicious": True}
    event["integrity"]["payload_sha256"] = compute_payload_sha256(event["payload"])

    result = validate_incident_event(event)
    assert result.is_valid is False
    assert result.reason_code == TransportReasonCode.REJECTED_SCHEMA


def test_immutability_during_validation(valid_event):
    """Verify validation does not modify any part of the input event dictionary."""
    event_copy = copy.deepcopy(valid_event)
    _ = validate_incident_event(valid_event)
    assert valid_event == event_copy
