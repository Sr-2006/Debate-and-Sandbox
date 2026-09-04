"""Tests verifying exact contract parity between Laptop2 copied schema and Laptop1 Stage A source of truth."""

import json
from pathlib import Path
import pytest

from transport.contracts import TransportReasonCode, CANONICAL_BLOCKS
from transport.canonical_json import compute_payload_sha256
from transport.validation import validate_incident_event, SCHEMA_PATH


def test_schema_structure_and_properties_parity():
    """Verify schema structural invariants match Laptop1 Stage A fe9c6354a6d295f352a36faae165a07f37395db6."""
    assert SCHEMA_PATH.is_file()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)

    # Root properties
    assert schema["properties"]["schema_version"]["enum"] == ["1.0"]
    assert schema["properties"]["event_type"]["enum"] == ["autosre.incident.ready"]
    assert schema["additionalProperties"] is False

    expected_top_required = [
        "schema_version",
        "event_type",
        "event_id",
        "correlation_id",
        "incident_id",
        "source",
        "transport",
        "payload"
    ]
    assert schema["required"] == expected_top_required

    # Source properties
    source_schema = schema["properties"]["source"]
    assert source_schema["additionalProperties"] is False
    assert source_schema["required"] == ["engine"]
    assert set(source_schema["properties"].keys()) == {
        "engine",
        "dataset_version",
        "git_sha",
        "generated_at"
    }
    assert "version" not in source_schema["properties"]

    # Transport properties
    transport_schema = schema["properties"]["transport"]
    assert transport_schema["additionalProperties"] is False
    assert transport_schema["required"] == ["payload_sha256"]
    assert set(transport_schema["properties"].keys()) == {
        "payload_sha256",
        "sent_at",
        "attempt"
    }

    # Payload properties
    payload_schema = schema["properties"]["payload"]
    assert payload_schema["additionalProperties"] is False
    assert set(payload_schema["required"]) == set(CANONICAL_BLOCKS)
    assert set(payload_schema["properties"].keys()) == set(CANONICAL_BLOCKS)


def test_reject_event_with_source_version():
    """Verify that an event containing source.version is strictly rejected with REJECTED_SCHEMA."""
    payload = {
        "system_context": {"objective": "RCA"},
        "incident_event": {"incident_id": "case_ver_rej", "severity": "HIGH"},
        "infrastructure_topology": {"role": "worker"},
        "service_health_status": {"health": "degraded"},
        "telemetry_evidence": {"log_samples": []},
        "injected_chaos_context": {"active_mutations": "none"}
    }
    payload_hash = compute_payload_sha256(payload)

    event_with_version = {
        "schema_version": "1.0",
        "event_type": "autosre.incident.ready",
        "event_id": "evt_reject_version",
        "correlation_id": "corr_rej_01",
        "incident_id": "case_ver_rej",
        "source": {
            "engine": "laptop1",
            "version": "1.0.0"  # Invalid field not in Stage A schema
        },
        "transport": {
            "payload_sha256": payload_hash,
            "sent_at": "2026-09-04T00:00:00Z"
        },
        "payload": payload
    }

    res = validate_incident_event(event_with_version)
    assert res.is_valid is False
    assert res.reason_code == TransportReasonCode.REJECTED_SCHEMA
    assert any("Additional properties are not allowed ('version' was unexpected)" in err or "version" in err for err in res.errors)
