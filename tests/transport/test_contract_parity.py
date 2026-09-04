"""Tests verifying exact contract parity between Laptop2 schema and Laptop1 Stage A incident.ready.v1 source of truth."""

import json
from pathlib import Path
import pytest

from transport.contracts import TransportReasonCode, CANONICAL_BLOCKS
from transport.canonical_json import compute_payload_sha256
from transport.validation import validate_incident_event, SCHEMA_PATH


def test_schema_structure_and_properties_parity():
    """Verify schema structural invariants match Laptop1 Stage A incident.ready.v1."""
    assert SCHEMA_PATH.is_file()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)

    # Root properties
    assert set(schema["properties"]["schema_version"]["enum"]) == {"1.0", "1.0.0"}
    assert schema["properties"]["event_type"]["enum"] == ["autosre.incident.ready.v1"]
    assert schema["additionalProperties"] is False

    expected_top_required = [
        "schema_version",
        "event_type",
        "event_id",
        "root_event_id",
        "correlation_id",
        "incident_id",
        "phase",
        "component",
        "status",
        "timestamp",
        "source",
        "integrity",
        "payload"
    ]
    assert set(schema["required"]) == set(expected_top_required)

    # Source properties
    source_schema = schema["properties"]["source"]
    assert source_schema["additionalProperties"] is False
    assert source_schema["required"] == ["engine"]
    assert set(source_schema["properties"].keys()) == {
        "engine",
        "host",
        "version",
        "dataset_version",
        "git_sha",
        "generated_at"
    }

    # Integrity properties
    integrity_schema = schema["properties"]["integrity"]
    assert integrity_schema["additionalProperties"] is False
    assert integrity_schema["required"] == ["payload_sha256"]
    assert set(integrity_schema["properties"].keys()) == {
        "payload_sha256",
        "signature",
        "commit_sha"
    }

    # Payload properties
    payload_schema = schema["properties"]["payload"]
    assert payload_schema["additionalProperties"] is False
    assert set(payload_schema["required"]) == set(CANONICAL_BLOCKS)
    assert set(payload_schema["properties"].keys()) == set(CANONICAL_BLOCKS) | {
        "laptop1_rl_advisory",
        "lineage_metadata",
        "phase1_summary",
        "phase2_summary"
    }


def test_reject_event_with_unknown_top_level_field():
    """Verify that an event containing unexpected top-level fields is strictly rejected with REJECTED_SCHEMA."""
    payload = {
        "system_context": {"objective": "RCA"},
        "incident_event": {"incident_id": "case_extra_rej", "severity": "HIGH"},
        "infrastructure_topology": {"role": "worker"},
        "service_health_status": {"health": "degraded"},
        "telemetry_evidence": {"log_samples": []},
        "injected_chaos_context": {"active_mutations": "none"}
    }
    payload_hash = compute_payload_sha256(payload)

    event_with_extra = {
        "schema_version": "1.0.0",
        "event_type": "autosre.incident.ready.v1",
        "event_id": "evt_reject_extra",
        "root_event_id": "evt_reject_extra",
        "parent_event_id": None,
        "correlation_id": "corr_rej_01",
        "incident_id": "case_extra_rej",
        "phase": "STAGE_A",
        "component": "incident_engine",
        "status": "READY",
        "timestamp": "2026-09-04T00:00:00Z",
        "source": {
            "engine": "laptop1",
            "host": "laptop1",
            "version": "1.0.0"
        },
        "integrity": {
            "payload_sha256": payload_hash,
            "signature": None,
            "commit_sha": None
        },
        "payload": payload,
        "unauthorized_arbitrary_field": "dangerous"
    }

    res = validate_incident_event(event_with_extra)
    assert res.is_valid is False
    assert res.reason_code == TransportReasonCode.REJECTED_SCHEMA
    assert any("unauthorized_arbitrary_field" in err for err in res.errors)
