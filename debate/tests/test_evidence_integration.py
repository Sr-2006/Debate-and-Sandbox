"""Tests for the Phase 1/2 -> Phase 3 evidence integration layer.

Covers:
- EvidenceLoader folder assembly (six blocks + Phase 2 extras)
- Dataset extraction (hottest-incident default + explicit selection)
- Contract guarantees (empty metrics_snapshot, blank template guard, id pattern)
- ActionPublisher envelope (veto cap at 64%, human review routing, offline fallback)
"""

import json
from pathlib import Path

import pytest

from evidence_loader import EvidenceLoader, CANONICAL_BLOCKS
from action_publisher import build_action_proposed, ActionPublisher, VETO_CONFIDENCE_CAP


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def evidence_folder(tmp_path: Path) -> Path:
    """Assemble a realistic debate_evidence/<incident_id>/ folder."""
    folder = tmp_path / "debate_evidence" / "payment-service_14"
    (folder / "logs").mkdir(parents=True)
    (folder / "metrics").mkdir(parents=True)

    incident_context = {
        "system_context": {
            "objective": "Perform automated RCA.",
            "environment": "Dockerized Microservices",
            "current_health_score": 40,
            "active_warnings": 8,
        },
        "incident_event": {
            "incident_id": "payment-service_14",
            "target_service": "payment-service",
            "priority_score": 92.0,
            "severity": "CRITICAL",
            "occurrence_count": 500,
        },
        "infrastructure_topology": {
            "role": "payment-processing",
            "downstream_dependencies": ["postgres-db", "rabbitmq"],
            "exposed_ports": ["8082:8082"],
        },
        "service_health_status": {
            "docker_status": "running",
            "health_check": "failing",
            "dependency_states": {"postgres-db": {"status": "running", "health": "unhealthy"}},
        },
        "telemetry_evidence": {
            "log_cluster_template": "ERROR [payment-service] Connection refused to postgres-db:5432",
            "log_samples": [
                {
                    "timestamp": "2026-08-18T10:00:00Z",
                    "level": "ERROR",
                    "content": "Connection refused to postgres-db:5432",
                    "trace_id": "trace-pg-01",
                    "span_id": "span-pg-01",
                }
            ],
            "metrics_snapshot": [],  # fresh cluster -> empty is legal
        },
        "injected_chaos_context": {
            "active_infrastructure_mutations": "PostgreSQL connection limit reduced to 5."
        },
    }
    (folder / "incident_context.json").write_text(json.dumps(incident_context), encoding="utf-8")

    enriched = {
        "event_type": "incident.enriched",
        "incident_id": "payment-service_14",
        "correlation_id": "corr-abc-123",
        "payload": {
            "fingerprint": "sha256-deadbeef",
            "similar_incidents": [
                {"incident_id": "payment-service_9", "similarity": 0.91, "resolution": "raised max_connections"}
            ],
            "historical_context": "Past PG exhaustion resolved by connection pooling.",
        },
    }
    (folder / "enriched_incident.json").write_text(json.dumps(enriched), encoding="utf-8")

    time_series = [{"timestamp": "2026-08-18T09:55:00Z", "cpu_percent": 40.0, "memory_percent": 70.0}]
    (folder / "metrics" / "time_series.json").write_text(json.dumps(time_series), encoding="utf-8")

    return folder


@pytest.fixture
def dataset_file(tmp_path: Path) -> Path:
    """A minimal unified_master_dataset.json, pre-sorted descending by priority."""
    dataset = {
        "metadata": {"dataset_version": "1.4.0", "git_sha": "abc123"},
        "incidents": [
            {
                "system_context": {"current_health_score": 30, "active_warnings": 9},
                "incident_event": {
                    "incident_id": "api-gateway_2",
                    "target_service": "api-gateway",
                    "priority_score": 95.0,
                    "severity": "CRITICAL",
                    "occurrence_count": 900,
                },
                "telemetry_evidence": {
                    "log_cluster_template": "ERROR Connection reset",
                    "log_samples": [],
                    "metrics_snapshot": [],
                },
            },
            {
                "incident_event": {
                    "incident_id": "order-service_7",
                    "target_service": "order-service",
                    "priority_score": 60.0,
                    "severity": "HIGH",
                    "occurrence_count": 100,
                },
                "telemetry_evidence": {
                    "log_cluster_template": "WARN queue backlog",
                    "log_samples": [],
                    "metrics_snapshot": [],
                },
            },
        ],
    }
    path = tmp_path / "unified_master_dataset.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# EvidenceLoader: folder assembly
# --------------------------------------------------------------------------- #
def test_load_from_folder_assembles_all_canonical_blocks(evidence_folder):
    payload = EvidenceLoader.load_from_folder(evidence_folder)
    for block in CANONICAL_BLOCKS:
        assert block in payload, f"missing block {block}"
    assert payload["incident_event"]["incident_id"] == "payment-service_14"


def test_load_from_folder_merges_phase2_extras(evidence_folder):
    payload = EvidenceLoader.load_from_folder(evidence_folder)
    assert payload["correlation_id"] == "corr-abc-123"
    assert payload["fingerprint"] == "sha256-deadbeef"
    assert payload["similar_incidents"][0]["incident_id"] == "payment-service_9"
    assert "historical_context" in payload


def test_load_from_folder_attaches_time_series(evidence_folder):
    payload = EvidenceLoader.load_from_folder(evidence_folder)
    assert payload["telemetry_evidence"]["time_series"][0]["cpu_percent"] == 40.0


def test_load_from_folder_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        EvidenceLoader.load_from_folder(tmp_path / "does_not_exist")


# --------------------------------------------------------------------------- #
# EvidenceLoader: dataset extraction
# --------------------------------------------------------------------------- #
def test_dataset_default_selects_hottest_incident(dataset_file):
    payload = EvidenceLoader.load_from_dataset(dataset_file)
    assert payload["incident_event"]["incident_id"] == "api-gateway_2"
    assert payload["metadata"]["git_sha"] == "abc123"


def test_dataset_explicit_incident_selection(dataset_file):
    payload = EvidenceLoader.load_from_dataset(dataset_file, "order-service_7")
    assert payload["incident_event"]["incident_id"] == "order-service_7"


def test_dataset_unknown_incident_raises(dataset_file):
    with pytest.raises(KeyError):
        EvidenceLoader.load_from_dataset(dataset_file, "nope_99")


# --------------------------------------------------------------------------- #
# Contract guarantees / normalization
# --------------------------------------------------------------------------- #
def test_empty_metrics_snapshot_is_preserved_not_crashed(evidence_folder):
    payload = EvidenceLoader.load_from_folder(evidence_folder)
    assert payload["telemetry_evidence"]["metrics_snapshot"] == []
    warnings = EvidenceLoader.validate(payload)
    # empty metrics is allowed -> only a soft warning, never an error
    assert any("metrics_snapshot" in w for w in warnings)


def test_blank_template_gets_placeholder_and_warning():
    payload = EvidenceLoader.normalize({"telemetry_evidence": {"log_cluster_template": ""}})
    assert payload["telemetry_evidence"]["log_cluster_template"] == "NO_CLUSTER_TEMPLATE"
    warnings = EvidenceLoader.validate(payload)
    assert any("log_cluster_template" in w for w in warnings)


def test_valid_incident_id_pattern_passes(evidence_folder):
    payload = EvidenceLoader.load_from_folder(evidence_folder)
    warnings = EvidenceLoader.validate(payload)
    assert not any("incident_id" in w for w in warnings)


def test_bad_incident_id_flagged():
    payload = EvidenceLoader.normalize(
        {"incident_event": {"incident_id": "random-uuid-1234"}, "telemetry_evidence": {"log_cluster_template": "ERROR x"}}
    )
    warnings = EvidenceLoader.validate(payload)
    assert any("incident_id" in w for w in warnings)


# --------------------------------------------------------------------------- #
# ActionPublisher: output contract
# --------------------------------------------------------------------------- #
def _sample_result(safety_violation: bool = False, confidence: int = 90) -> dict:
    return {
        "solution": {
            "consensus_rc": "PostgreSQL connection exhaustion",
            "primary_component": "database",
            "action_commands": ["systemctl restart postgresql"],
            "final_rca": "Connection pool saturated.",
            "consensus_quality": "HIGH",
            "scoring_metadata": {"veto_reason": None},
        },
        "confidence_score": confidence,
        "execution_tier": "TIER_1_AUTONOMOUS_EXECUTION",
        "safety_violation": safety_violation,
        "round_2_executed": False,
        "total_latency_seconds": 12.5,
    }


def test_action_envelope_shape():
    msg = build_action_proposed("payment-service_14", _sample_result(), correlation_id="corr-1", fingerprint="fp-1")
    assert msg["event_type"] == "autosre.action.proposed"
    assert msg["incident_id"] == "payment-service_14"

    assert msg["correlation_id"] == "corr-1"
    assert msg["payload"]["confidence"] == 90
    assert msg["payload"]["execution_tier"] == "TIER_1_AUTONOMOUS_EXECUTION"


def test_veto_caps_confidence_at_64_and_routes_human_review():
    msg = build_action_proposed("payment-service_14", _sample_result(safety_violation=True, confidence=97))
    assert msg["payload"]["confidence"] == VETO_CONFIDENCE_CAP == 64
    assert msg["payload"]["execution_tier"] == "HUMAN_REVIEW"
    assert msg["payload"]["safety_violation"] is True


def test_offline_publish_writes_file(tmp_path):
    msg = build_action_proposed("payment-service_14", _sample_result())
    publisher = ActionPublisher(rabbitmq_url="amqp://guest:guest@127.0.0.1:1/")  # unreachable -> fallback
    status = publisher.publish(msg, offline_dir=str(tmp_path))
    assert status["ok"] is True
    out_file = tmp_path / "payment-service_14.action_proposed.json"
    assert out_file.exists()
    written = json.loads(out_file.read_text(encoding="utf-8"))
    assert written["payload"]["confidence"] == 90
