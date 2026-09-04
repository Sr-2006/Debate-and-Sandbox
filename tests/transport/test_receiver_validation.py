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
            "sanitized": True,
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


def test_live_style_event_corr_b324_shape_passes(valid_event):
    """Verify exact live-style shape from corr_b324a7f776d04783837e3d612e6bfa78 is accepted."""
    event = copy.deepcopy(valid_event)
    event["event_id"] = "evt_3bb1bed38c6d479ea3f8c84e1ed2fa72"
    event["correlation_id"] = "corr_b324a7f776d04783837e3d612e6bfa78"
    event["incident_id"] = "order-service_51"
    event["phase"] = "laptop1_handoff"
    event["root_event_id"] = "evt_1c12c5d6d5334e2c8a4add523233ca3a"
    event["schema_version"] = "1.0"
    event["source"] = {
        "engine": "laptop1",
        "git_sha": "cffb15e2b6e30e834bbb5c84eb355e42c99f9094",
        "version": "1.0.0"
    }
    event["status"] = "SUCCESS"
    event["timestamp"] = "2026-09-04T17:22:20.869800+00:00"
    event["payload"]["incident_event"]["incident_id"] = "order-service_51"
    event["integrity"] = {
        "payload_sha256": compute_payload_sha256(event["payload"]),
        "sanitized": True
    }

    result = validate_incident_event(event)
    assert result.is_valid is True
    assert result.reason_code == TransportReasonCode.VALID


def test_integrity_sanitized_boolean_and_null_allowed(valid_event):
    """Verify integrity.sanitized accepts True, False, and None."""
    for val in [True, False, None]:
        event = copy.deepcopy(valid_event)
        event["integrity"]["sanitized"] = val
        result = validate_incident_event(event)
        assert result.is_valid is True
        assert result.reason_code == TransportReasonCode.VALID


def test_sanitized_at_invalid_paths_rejected(valid_event):
    """Verify sanitized placed at root, source, or payload is strictly rejected."""
    # 1. At root
    event1 = copy.deepcopy(valid_event)
    del event1["integrity"]["sanitized"]
    event1["sanitized"] = True
    res1 = validate_incident_event(event1)
    assert res1.is_valid is False
    assert res1.reason_code == TransportReasonCode.REJECTED_SCHEMA

    # 2. In source
    event2 = copy.deepcopy(valid_event)
    del event2["integrity"]["sanitized"]
    event2["source"]["sanitized"] = True
    res2 = validate_incident_event(event2)
    assert res2.is_valid is False
    assert res2.reason_code == TransportReasonCode.REJECTED_SCHEMA

    # 3. In payload
    event3 = copy.deepcopy(valid_event)
    del event3["integrity"]["sanitized"]
    event3["payload"]["sanitized"] = True
    event3["integrity"]["payload_sha256"] = compute_payload_sha256(event3["payload"])
    res3 = validate_incident_event(event3)
    assert res3.is_valid is False
    assert res3.reason_code == TransportReasonCode.REJECTED_SCHEMA


def test_sanitized_invalid_type_rejected(valid_event):
    """Verify non-boolean integrity.sanitized is rejected."""
    event = copy.deepcopy(valid_event)
    event["integrity"]["sanitized"] = "yes"
    result = validate_incident_event(event)
    assert result.is_valid is False
    assert result.reason_code == TransportReasonCode.REJECTED_SCHEMA


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
