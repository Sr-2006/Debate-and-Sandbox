"""Unit tests for Phase 3/4 completion result event builder and validation."""

import copy
import glob
import hashlib
import json
import os
from pathlib import Path
import pytest

from shadow_sandbox.reports.report_generator import compute_report_hash
from transport.result_publisher import (
    build_phase34_completed_event,
    validate_completed_event,
    load_completed_schema,
    compute_event_log_hash_for_report
)


TARGET_REPORT_PATH = "reports/verify_44da93e0765a47a0902be74e08377a53/cases/order-service_51/phase34_report.json"
TARGET_EVENT_LOG_PATH = "reports/verify_44da93e0765a47a0902be74e08377a53/cases/order-service_51/phase34_events.jsonl"
SAMPLE_INPUT_PAYLOAD_HASH = "4792cf0966aece1aed1724ac89b12a63bd862da7cfd25725144728fd5130ea98"


@pytest.fixture
def target_report():
    """Loads the exact target report from disk."""
    with open(TARGET_REPORT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_build_phase34_completed_event_validity(target_report):
    """Verify built completed event matches phase34_completed_v1 schema exactly."""
    parent_event_id = "evt_70cbb2cc70b04630ae49698d7fac19f7"
    correlation_id = "corr_2e1ea3d8df5345dcb3b15f623c51e827"

    event = build_phase34_completed_event(
        report=target_report,
        parent_event_id=parent_event_id,
        correlation_id=correlation_id,
        input_payload_sha256=SAMPLE_INPUT_PAYLOAD_HASH,
        report_path=TARGET_REPORT_PATH
    )

    validation = validate_completed_event(event)
    assert validation.is_valid is True, f"Validation failed with errors: {validation.errors}"

    assert event["schema_version"] == "v1"
    assert event["event_type"] == "autosre.phase34.completed"
    assert event["incident_id"] == "order-service_51"
    assert event["parent_event_id"] == parent_event_id
    assert event["correlation_id"] == correlation_id
    assert event["source"]["engine"] == "laptop2"
    assert len(event["source"]["git_sha"]) >= 7
    assert event["final_outcome"] == "SANDBOX_VERIFIED"


def test_exact_phase3_values_projected(target_report):
    """Verify exact Phase 3 values from report are projected without alteration."""
    parent_event_id = "evt_70cbb2cc70b04630ae49698d7fac19f7"
    correlation_id = "corr_2e1ea3d8df5345dcb3b15f623c51e827"

    event = build_phase34_completed_event(
        report=target_report,
        parent_event_id=parent_event_id,
        correlation_id=correlation_id,
        input_payload_sha256=SAMPLE_INPUT_PAYLOAD_HASH,
        report_path=TARGET_REPORT_PATH
    )

    p3_source = target_report["phase_3"]
    assert event["phase_3"]["status"] == p3_source["status"]
    assert event["phase_3"]["confidence_score"] == p3_source["confidence"]["score"]
    assert event["phase_3"]["orchestrator_decision"] == p3_source["orchestrator_decision"]
    assert event["phase_3"]["safety_status"] == p3_source["safety"]["status"]
    assert event["phase_3"]["selected_intent"]["intent_type"] == p3_source["selected_intent"]["intent_type"]
    assert event["phase_3"]["selected_intent"]["parameters"] == p3_source["selected_intent"]["parameters"]


def test_exact_rl_values_projected(target_report):
    """Verify exact RL values from report are projected without recomputation."""
    parent_event_id = "evt_70cbb2cc70b04630ae49698d7fac19f7"
    correlation_id = "corr_2e1ea3d8df5345dcb3b15f623c51e827"

    event = build_phase34_completed_event(
        report=target_report,
        parent_event_id=parent_event_id,
        correlation_id=correlation_id,
        input_payload_sha256=SAMPLE_INPUT_PAYLOAD_HASH,
        report_path=TARGET_REPORT_PATH
    )

    rl_source = target_report["rl_advisory"]
    assert event["rl_advisory"]["status"] == rl_source["status"]
    assert event["rl_advisory"]["operating_mode"] == rl_source["operating_mode"]
    assert event["rl_advisory"]["advisory_decision"] == rl_source["recommendation"]
    assert event["rl_advisory"]["feature_hash"] == rl_source["feature_hash"]
    assert event["rl_advisory"]["advisory_confidence"] == rl_source["action_scores"][rl_source["recommendation"]]


def test_exact_phase4_values_projected(target_report):
    """Verify exact Phase 4 values from report are projected without alteration."""
    parent_event_id = "evt_70cbb2cc70b04630ae49698d7fac19f7"
    correlation_id = "corr_2e1ea3d8df5345dcb3b15f623c51e827"

    event = build_phase34_completed_event(
        report=target_report,
        parent_event_id=parent_event_id,
        correlation_id=correlation_id,
        input_payload_sha256=SAMPLE_INPUT_PAYLOAD_HASH,
        report_path=TARGET_REPORT_PATH
    )

    p4_source = target_report["phase_4"]
    assert event["phase_4"]["status"] == p4_source["status"]
    assert event["phase_4"]["attestation_status"] == p4_source["attestation"]["status"]
    assert event["phase_4"]["execution_status"] == p4_source["execution"]["status"]
    assert event["phase_4"]["execution_capability"] == p4_source["execution"]["capability"]
    assert event["phase_4"]["verification_status"] == p4_source["verification"]["status"]
    assert event["phase_4"]["rollback_status"] == p4_source["rollback"]["status"]


def test_real_event_log_hash_non_empty(target_report):
    """Verify event log hash is computed from real non-empty file and not SHA256(empty)."""
    parent_event_id = "evt_70cbb2cc70b04630ae49698d7fac19f7"
    correlation_id = "corr_2e1ea3d8df5345dcb3b15f623c51e827"

    event = build_phase34_completed_event(
        report=target_report,
        parent_event_id=parent_event_id,
        correlation_id=correlation_id,
        input_payload_sha256=SAMPLE_INPUT_PAYLOAD_HASH,
        report_path=TARGET_REPORT_PATH
    )

    empty_hash = hashlib.sha256(b"").hexdigest()
    assert event["integrity"]["events_log_hash"] != empty_hash
    assert event["integrity"]["events_log_hash"] == "8126395ce65846d6a0b3a5d22bb7aa4d8736bb5291dc570e47819882ee96f5c8"
    assert event["integrity"]["report_hash"] == "23223dc040290393e5065a10002a7de19ca9b6d79b05f5aa8cb7893849721643"
    assert event["integrity"]["input_payload_sha256"] == SAMPLE_INPUT_PAYLOAD_HASH


def test_missing_event_log_fails_clearly():
    """Verify missing event log raises an error instead of silently hashing empty bytes."""
    mock_report = {
        "problem": {"case_id": "mock_case"},
        "final_summary": {"outcome": "SANDBOX_VERIFIED"},
        "phase_3": {"status": "COMPLETED", "confidence": {"score": 0.9}, "orchestrator_decision": "TIER_1", "safety": {"status": "PASS"}},
        "rl_advisory": {"status": "SUCCESS", "operating_mode": "SHADOW", "recommendation": "ALLOW", "feature_hash": "a"*64},
        "phase_4": {"status": "SANDBOX_VERIFIED", "target": {"kind": "database"}, "attestation": {"status": "PASSED"}, "execution": {"status": "SUCCESS", "capability": "c"}, "verification": {"status": "PASSED"}, "rollback": {"status": "NOT_RUN"}},
        "integrity": {}
    }
    mock_report["integrity"]["report_hash"] = compute_report_hash(mock_report)

    with pytest.raises(ValueError, match="Event log file phase34_events.jsonl is missing"):
        build_phase34_completed_event(
            report=mock_report,
            parent_event_id="evt_1234567890abcdef1234567890abcdef",
            correlation_id="corr_1234567890abcdef1234567890abcdef",
            input_payload_sha256="b" * 64,
            report_path="nonexistent_dir/report.json"
        )


def test_no_agent_chain_of_thought_leaked(target_report):
    """Verify no internal prompts, reasoning, or raw CoT text are included in the result event."""
    parent_event_id = "evt_70cbb2cc70b04630ae49698d7fac19f7"
    correlation_id = "corr_2e1ea3d8df5345dcb3b15f623c51e827"

    event = build_phase34_completed_event(
        report=target_report,
        parent_event_id=parent_event_id,
        correlation_id=correlation_id,
        input_payload_sha256=SAMPLE_INPUT_PAYLOAD_HASH,
        report_path=TARGET_REPORT_PATH
    )

    event_str = json.dumps(event)
    assert "agent_prompts" not in event_str
    assert "raw_response" not in event_str
    assert "chain_of_thought" not in event_str
    assert "cot" not in event_str.lower()
    assert "scratchpad" not in event_str.lower()
