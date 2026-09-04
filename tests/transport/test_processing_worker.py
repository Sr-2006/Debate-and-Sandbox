"""Unit tests for Laptop2 automated remediation processing worker."""

import asyncio
import copy
import hashlib
import json
import os
import shutil
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from transport.contracts import ProcessingStatus, EventStatus
from transport.canonical_json import compute_payload_sha256
from transport.dedup_store import DedupStore
from transport.processing_worker import Laptop2ProcessingWorker
from shadow_sandbox.reports.report_generator import compute_report_hash


SAMPLE_REPORT_TEMPLATE = {
    "schema_version": "phase34-report-v1",
    "report_type": "PHASE34_PROBLEM_SUMMARY",
    "run": {
        "verification_run_id": "verify_test_123456",
        "problem_run_id": "prob_test_123456",
        "commit_sha": "ce1d50d8252fe4f160a82fbcfacead9458855a13",
        "started_at": "2026-09-04T08:00:00Z",
        "completed_at": "2026-09-04T08:00:05Z",
        "duration_ms": 5000.0,
        "execution_mode": "REAL_SHADOW",
        "mock_llm": False,
        "rl_operating_mode": "SHADOW",
        "laptop1_transport": None
    },
    "problem": {
        "case_id": "order-service_51",
        "source_file": "runtime/transport_inputs/evt_test.json",
        "input_hash": "a" * 64,
        "raw_input": {"incident_id": "order-service_51"},
        "severity": "HIGH",
        "target": {"kind": "database", "canonical_name": "postgres-db"},
        "expected_behavior": "normal"
    },
    "phase_3": {
        "status": "COMPLETED",
        "confidence": {"score": 0.9},
        "orchestrator_decision": "TIER_1_AUTONOMOUS_EXECUTION",
        "safety": {"status": "PASS"},
        "selected_intent": {
            "intent_type": "postgres.setting.update",
            "mode": "MUTATE_REVERSIBLE",
            "target_ref": {"kind": "database", "canonical_name": "postgres-db"},
            "parameters": {"setting_name": "max_connections", "value": "200"}
        }
    },
    "phase_3_to_4_handoff": {"payload_hash": "b" * 64},
    "rl_advisory": {
        "status": "SUCCESS",
        "operating_mode": "SHADOW",
        "recommendation": "OBSERVE_FIRST",
        "action_scores": {"OBSERVE_FIRST": 0.5},
        "feature_schema_version": "features-v2",
        "feature_hash": "c" * 64
    },
    "phase_4": {
        "status": "SANDBOX_VERIFIED",
        "target": {"kind": "database", "canonical_name": "postgres-db"},
        "attestation": {"status": "PASSED"},
        "execution": {"status": "SUCCESS", "capability": "postgres.setting.update"},
        "verification": {"status": "PASSED"},
        "rollback": {"status": "NOT_RUN"}
    },
    "learning": {"status": "NOT_RUN"},
    "final_summary": {
        "outcome": "SANDBOX_VERIFIED",
        "problem_resolved_in_sandbox": True,
        "execution_performed": True,
        "human_intervention_required": False
    },
    "integrity": {}
}


@pytest.fixture
def test_env(tmp_path):
    """Sets up a clean test database and sample staged files."""
    db_path = str(tmp_path / "transport.db")
    inputs_dir = tmp_path / "transport_inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = tmp_path / "reports" / "verify_test_123456" / "cases" / "order-service_51"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Canonical 6-block payload
    payload = {
        "system_context": {"component": "order"},
        "incident_event": {"incident_id": "order-service_51", "severity": "HIGH"},
        "infrastructure_topology": {"nodes": []},
        "service_health_status": {"status": "degraded", "dependency_states": {"db": "healthy"}},
        "telemetry_evidence": {"metrics": []},
        "injected_chaos_context": {"chaos": False}
    }
    payload_hash = compute_payload_sha256(payload)

    # Full transport envelope
    input_file = inputs_dir / "evt_test1234567890abcdef1234567890ab.json"
    input_data = {
        "schema_version": "1.0",
        "event_id": "evt_test1234567890abcdef1234567890ab",
        "correlation_id": "corr_test1234567890abcdef1234567890a",
        "incident_id": "order-service_51",
        "event_type": "autosre.incident.ready",
        "source": {"engine": "laptop1"},
        "transport": {"payload_sha256": payload_hash},
        "payload": payload
    }
    input_bytes = json.dumps(input_data, indent=2).encode("utf-8")
    input_file.write_bytes(input_bytes)

    # Sample event log and canonical report
    events_file = reports_dir / "phase34_events.jsonl"
    events_content = b'{"timestamp":"2026-09-04T08:00:00Z","event":"INIT"}\n'
    events_file.write_bytes(events_content)
    events_hash = hashlib.sha256(events_content).hexdigest()

    report_file = reports_dir / "phase34_report.json"
    rep = copy.deepcopy(SAMPLE_REPORT_TEMPLATE)
    rep["integrity"]["event_log_hash"] = events_hash
    rep["integrity"]["report_hash"] = compute_report_hash(rep)
    report_file.write_text(json.dumps(rep, indent=2), encoding="utf-8")

    dedup = DedupStore(db_path)
    dedup.record_received(
        event_id="evt_test1234567890abcdef1234567890ab",
        incident_id="order-service_51",
        payload_hash=payload_hash,
        correlation_id="corr_test1234567890abcdef1234567890a"
    )
    dedup.mark_staged("evt_test1234567890abcdef1234567890ab", str(input_file))

    return {
        "db_path": db_path,
        "input_file": str(input_file),
        "input_bytes": input_bytes,
        "input_data": input_data,
        "payload": payload,
        "payload_hash": payload_hash,
        "report_file": str(report_file),
        "events_file": str(events_file),
        "reports_base_dir": str(tmp_path / "reports"),
        "parent_event_id": "evt_test1234567890abcdef1234567890ab",
        "correlation_id": "corr_test1234567890abcdef1234567890a",
        "incident_id": "order-service_51"
    }


def test_canonical_payload_hash_matches_received_events(test_env):
    """Verify worker validates canonical payload hash from payload block rather than raw file bytes."""
    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])
    is_valid, err_code, err_msg, raw_sha, env = worker._verify_staged_input_and_identity(
        input_path=test_env["input_file"],
        expected_parent_event_id=test_env["parent_event_id"],
        expected_correlation_id=test_env["correlation_id"],
        expected_incident_id=test_env["incident_id"],
        expected_payload_hash=test_env["payload_hash"]
    )
    assert is_valid is True
    assert err_code is None
    assert raw_sha == hashlib.sha256(test_env["input_bytes"]).hexdigest()
    assert raw_sha != test_env["payload_hash"]  # Proves raw file SHA != payload hash


def test_envelope_whitespace_change_does_not_break_payload_hash(test_env):
    """Verify changing whitespace/formatting in the envelope leaves canonical payload hash valid."""
    # Re-serialize envelope with different indentation and extra top-level metadata
    mutated_env = copy.deepcopy(test_env["input_data"])
    mutated_env["unrelated_metadata"] = "extra_info"
    new_bytes = json.dumps(mutated_env, separators=(", ", ": ")).encode("utf-8")
    with open(test_env["input_file"], "wb") as f:
        f.write(new_bytes)

    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])
    is_valid, err_code, _, _, _ = worker._verify_staged_input_and_identity(
        input_path=test_env["input_file"],
        expected_parent_event_id=test_env["parent_event_id"],
        expected_correlation_id=test_env["correlation_id"],
        expected_incident_id=test_env["incident_id"],
        expected_payload_hash=test_env["payload_hash"]
    )
    assert is_valid is True


def test_tampered_payload_detected(test_env):
    """Verify modifying a field inside the payload triggers INPUT_HASH_MISMATCH."""
    tampered_env = copy.deepcopy(test_env["input_data"])
    tampered_env["payload"]["incident_event"]["severity"] = "LOW"
    with open(test_env["input_file"], "wb") as f:
        f.write(json.dumps(tampered_env).encode("utf-8"))

    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])
    is_valid, err_code, err_msg, _, _ = worker._verify_staged_input_and_identity(
        input_path=test_env["input_file"],
        expected_parent_event_id=test_env["parent_event_id"],
        expected_correlation_id=test_env["correlation_id"],
        expected_incident_id=test_env["incident_id"],
        expected_payload_hash=test_env["payload_hash"]
    )
    assert is_valid is False
    assert err_code == "INPUT_HASH_MISMATCH"


def test_wrong_staged_event_id_rejected(test_env):
    """Verify mismatched event_id in staged envelope triggers INPUT_IDENTITY_MISMATCH."""
    env = copy.deepcopy(test_env["input_data"])
    env["event_id"] = "evt_wrong1234567890abcdef1234567890"
    with open(test_env["input_file"], "wb") as f:
        f.write(json.dumps(env).encode("utf-8"))

    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])
    is_valid, err_code, _, _, _ = worker._verify_staged_input_and_identity(
        input_path=test_env["input_file"],
        expected_parent_event_id=test_env["parent_event_id"],
        expected_correlation_id=test_env["correlation_id"],
        expected_incident_id=test_env["incident_id"],
        expected_payload_hash=test_env["payload_hash"]
    )
    assert is_valid is False
    assert err_code == "INPUT_IDENTITY_MISMATCH"


def test_wrong_staged_correlation_id_rejected(test_env):
    """Verify mismatched correlation_id in staged envelope triggers INPUT_IDENTITY_MISMATCH."""
    env = copy.deepcopy(test_env["input_data"])
    env["correlation_id"] = "corr_wrong1234567890abcdef12345678"
    with open(test_env["input_file"], "wb") as f:
        f.write(json.dumps(env).encode("utf-8"))

    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])
    is_valid, err_code, _, _, _ = worker._verify_staged_input_and_identity(
        input_path=test_env["input_file"],
        expected_parent_event_id=test_env["parent_event_id"],
        expected_correlation_id=test_env["correlation_id"],
        expected_incident_id=test_env["incident_id"],
        expected_payload_hash=test_env["payload_hash"]
    )
    assert is_valid is False
    assert err_code == "INPUT_IDENTITY_MISMATCH"


def test_wrong_staged_incident_id_rejected(test_env):
    """Verify mismatched incident_id in staged envelope triggers INPUT_IDENTITY_MISMATCH."""
    env = copy.deepcopy(test_env["input_data"])
    env["incident_id"] = "other-service_99"
    with open(test_env["input_file"], "wb") as f:
        f.write(json.dumps(env).encode("utf-8"))

    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])
    is_valid, err_code, _, _, _ = worker._verify_staged_input_and_identity(
        input_path=test_env["input_file"],
        expected_parent_event_id=test_env["parent_event_id"],
        expected_correlation_id=test_env["correlation_id"],
        expected_incident_id=test_env["incident_id"],
        expected_payload_hash=test_env["payload_hash"]
    )
    assert is_valid is False
    assert err_code == "INPUT_IDENTITY_MISMATCH"


def test_staged_file_immutability_verified_after_processing(test_env):
    """Verify file immutability check passes when file bytes remain strictly identical."""
    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])
    raw_sha_before = hashlib.sha256(test_env["input_bytes"]).hexdigest()
    is_immut, err = worker._verify_file_immutability(test_env["input_file"], raw_sha_before)
    assert is_immut is True
    assert err is None


def test_file_tampering_during_execution_detected(test_env):
    """Verify mutating the staged file during pipeline execution triggers INPUT_FILE_TAMPERED."""
    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])

    def _tamper_subprocess(*args, **kwargs):
        with open(test_env["input_file"], "ab") as f:
            f.write(b"\n# mutated by test")
        return 0, "", "", {"json_report": test_env["report_file"]}

    with patch.object(worker, "_run_pipeline_subprocess", side_effect=_tamper_subprocess):
        res = worker.process_event(parent_event_id=test_env["parent_event_id"])

    assert res["status"] == "FAILED"
    assert res["error_code"] == "INPUT_FILE_TAMPERED"


def test_successful_worker_processing_flow(test_env):
    """Verify full end-to-end processing with mocked subprocess and publisher."""
    worker = Laptop2ProcessingWorker(
        state_db_path=test_env["db_path"],
        pipeline_timeout_seconds=60.0
    )

    mock_summary = {
        "incident_id": "order-service_51",
        "verification_run_id": "verify_test_123456",
        "outcome": "SANDBOX_VERIFIED",
        "json_report": test_env["report_file"],
        "events_report": test_env["events_file"]
    }

    with patch.object(worker, "_run_pipeline_subprocess", return_value=(0, "[PIPELINE_RESULT_JSON]\n" + json.dumps(mock_summary) + "\n[/PIPELINE_RESULT_JSON]", "", mock_summary)), \
         patch("transport.processing_worker.Laptop2ResultPublisher") as MockPublisher:

        mock_pub_inst = MagicMock()
        mock_ack = {"status": "PUBLISHED", "stream": "AUTOSRE", "seq": 100}
        mock_pub_inst.publish_result = AsyncMock(return_value=mock_ack)
        mock_pub_inst.close = AsyncMock()
        MockPublisher.return_value = mock_pub_inst

        res = worker.process_event(parent_event_id=test_env["parent_event_id"])

        assert res["status"] == "PROCESSING_COMPLETE"
        assert res["parent_event_id"] == test_env["parent_event_id"]
        assert res["correlation_id"] == test_env["correlation_id"]
        assert res["incident_id"] == test_env["incident_id"]
        assert res["final_outcome"] == "SANDBOX_VERIFIED"
        assert res["report_path"] == test_env["report_file"]

        # Verify DB final state is RESULT_PUBLISHED
        dedup = DedupStore(test_env["db_path"])
        state = dedup.get_processing_state(test_env["parent_event_id"])
        assert state["processing_status"] == "RESULT_PUBLISHED"
        assert state["report_path"] == test_env["report_file"]
        assert state["result_event_id"] == res["result_event_id"]


def test_semantic_dedup_skip_accepted_only_for_exact_match(test_env):
    """Verify semantic dedup skip is accepted when parent_event_id and report_hash match."""
    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])

    with open(test_env["report_file"], "r", encoding="utf-8") as f:
        rep_data = json.load(f)
    rep_hash = rep_data["integrity"]["report_hash"]

    mock_summary = {
        "incident_id": "order-service_51",
        "verification_run_id": "verify_test_123456",
        "outcome": "SANDBOX_VERIFIED",
        "json_report": test_env["report_file"],
        "events_report": test_env["events_file"]
    }

    mock_skip_ack = {
        "status": "SKIPPED_ALREADY_PUBLISHED",
        "parent_event_id": test_env["parent_event_id"],
        "report_hash": rep_hash,
        "event_id": "evt_existing1234567890abcdef123456"
    }

    with patch.object(worker, "_run_pipeline_subprocess", return_value=(0, "", "", mock_summary)), \
         patch("transport.processing_worker.Laptop2ResultPublisher") as MockPublisher:

        mock_pub_inst = MagicMock()
        mock_pub_inst.publish_result = AsyncMock(return_value=mock_skip_ack)
        mock_pub_inst.close = AsyncMock()
        MockPublisher.return_value = mock_pub_inst

        res = worker.process_event(parent_event_id=test_env["parent_event_id"])

        assert res["status"] == "PROCESSING_COMPLETE"
        assert res["result_event_id"] == "evt_existing1234567890abcdef123456"


def test_semantic_dedup_mismatch_fails(test_env):
    """Verify semantic dedup return with mismatched report_hash fails."""
    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])

    mock_summary = {
        "incident_id": "order-service_51",
        "verification_run_id": "verify_test_123456",
        "outcome": "SANDBOX_VERIFIED",
        "json_report": test_env["report_file"],
        "events_report": test_env["events_file"]
    }

    mock_mismatch_skip = {
        "status": "SKIPPED_ALREADY_PUBLISHED",
        "parent_event_id": test_env["parent_event_id"],
        "report_hash": "f" * 64,  # Mismatched hash
        "event_id": "evt_mismatch"
    }

    with patch.object(worker, "_run_pipeline_subprocess", return_value=(0, "", "", mock_summary)), \
         patch("transport.processing_worker.Laptop2ResultPublisher") as MockPublisher:

        mock_pub_inst = MagicMock()
        mock_pub_inst.publish_result = AsyncMock(return_value=mock_mismatch_skip)
        mock_pub_inst.close = AsyncMock()
        MockPublisher.return_value = mock_pub_inst

        res = worker.process_event(parent_event_id=test_env["parent_event_id"])

        assert res["status"] == "FAILED"
        assert res["error_code"] == "RESULT_DEDUP_MISMATCH"


def test_result_published_does_not_rerun(test_env):
    """Verify that once RESULT_PUBLISHED is recorded, subsequent runs skip without pipeline execution."""
    dedup = DedupStore(test_env["db_path"])
    dedup.claim_staged_event(test_env["parent_event_id"])
    dedup.mark_result_published(test_env["parent_event_id"], "evt_pub123", "a"*64)

    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])
    with patch.object(worker, "_run_pipeline_subprocess") as mock_pipe:
        res = worker.process_event(parent_event_id=test_env["parent_event_id"])
        assert res["status"] == "ALREADY_COMPLETED"
        assert mock_pipe.call_count == 0


def test_failed_requires_explicit_retry(test_env):
    """Verify that a FAILED event is not retried unless retry_failed=True."""
    dedup = DedupStore(test_env["db_path"])
    dedup.claim_staged_event(test_env["parent_event_id"])
    dedup.mark_processing_failed(test_env["parent_event_id"], "PIPELINE_EXIT_NONZERO", "Error")

    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])

    # Without retry_failed -> skipped
    res1 = worker.process_event(parent_event_id=test_env["parent_event_id"], retry_failed=False)
    assert res1["status"] == "FAILED_REQUIRES_RETRY"

    # With retry_failed -> successfully re-claims
    mock_summary = {
        "incident_id": "order-service_51",
        "verification_run_id": "verify_test_123456",
        "outcome": "SANDBOX_VERIFIED",
        "json_report": test_env["report_file"],
        "events_report": test_env["events_file"]
    }
    with patch.object(worker, "_run_pipeline_subprocess", return_value=(0, json.dumps(mock_summary), "", mock_summary)), \
         patch("transport.processing_worker.Laptop2ResultPublisher") as MockPublisher:

        mock_pub_inst = MagicMock()
        mock_pub_inst.publish_result = AsyncMock(return_value={"status": "PUBLISHED", "seq": 105})
        mock_pub_inst.close = AsyncMock()
        MockPublisher.return_value = mock_pub_inst

        res2 = worker.process_event(parent_event_id=test_env["parent_event_id"], retry_failed=True)
        assert res2["status"] == "PROCESSING_COMPLETE"


def test_stale_claim_fails_closed_unless_recover_stale(test_env):
    """Verify that stale PROCESSING records require recover_stale=True to be reclaimed."""
    dedup = DedupStore(test_env["db_path"])
    dedup.claim_staged_event(test_env["parent_event_id"])

    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])

    # Without recover_stale on an active claim -> skips with ALREADY_CLAIMED
    res1 = worker.process_event(parent_event_id=test_env["parent_event_id"], recover_stale=False)
    assert res1["status"] == "ALREADY_CLAIMED"

    # With recover_stale -> re-claims successfully
    mock_summary = {
        "incident_id": "order-service_51",
        "verification_run_id": "verify_test_123456",
        "outcome": "SANDBOX_VERIFIED",
        "json_report": test_env["report_file"],
        "events_report": test_env["events_file"]
    }
    with patch.object(worker, "_run_pipeline_subprocess", return_value=(0, json.dumps(mock_summary), "", mock_summary)), \
         patch("transport.processing_worker.Laptop2ResultPublisher") as MockPublisher:

        mock_pub_inst = MagicMock()
        mock_pub_inst.publish_result = AsyncMock(return_value={"status": "PUBLISHED", "seq": 106})
        mock_pub_inst.close = AsyncMock()
        MockPublisher.return_value = mock_pub_inst

        res2 = worker.process_event(parent_event_id=test_env["parent_event_id"], recover_stale=True)
        assert res2["status"] == "PROCESSING_COMPLETE"


def test_identity_mismatch_blocks_pipeline_and_publish(test_env):
    """Verify regression: parent_event_id pointing to old event staged envelope triggers INPUT_IDENTITY_MISMATCH and blocks pipeline."""
    dedup = DedupStore(test_env["db_path"])

    # Record a new event pointing to the old event envelope file
    new_event_id = "evt_new_event_1234567890abcdef12345"
    dedup.record_received(
        event_id=new_event_id,
        incident_id=test_env["incident_id"],
        payload_hash=test_env["payload_hash"],
        correlation_id="corr_new_event_1234567890abcdef123"
    )
    # Point input_path to test_env["input_file"] which has event_id="evt_test1234567890abcdef1234567890ab"
    dedup.mark_staged(new_event_id, test_env["input_file"])

    worker = Laptop2ProcessingWorker(state_db_path=test_env["db_path"])

    with patch.object(worker, "_run_pipeline_subprocess") as mock_pipeline, \
         patch("transport.processing_worker.Laptop2ResultPublisher") as MockPublisher:

        res = worker.process_event(parent_event_id=new_event_id)

        assert res["status"] == "FAILED"
        assert res["error_code"] == "INPUT_IDENTITY_MISMATCH"
        assert "does not match expected parent_event_id" in res["message"]

        # Pipeline and result publisher MUST NOT be invoked
        assert mock_pipeline.call_count == 0
        assert MockPublisher.call_count == 0

        # Verify DB state is recorded as FAILED
        state = dedup.get_processing_state(new_event_id)
        assert state["processing_status"] == "FAILED"
        assert state["last_error_code"] == "INPUT_IDENTITY_MISMATCH"


def test_nats_url_propagated_to_publisher(test_env):
    """Verify configured NATS URL is passed to Laptop2ResultPublisher without localhost fallback."""
    explicit_nats_url = "nats://172.51.154.253:4222"
    worker = Laptop2ProcessingWorker(
        state_db_path=test_env["db_path"],
        nats_url=explicit_nats_url
    )

    mock_summary = {
        "incident_id": "order-service_51",
        "verification_run_id": "verify_test_123456",
        "outcome": "SANDBOX_VERIFIED",
        "json_report": test_env["report_file"],
        "events_report": test_env["events_file"]
    }

    with patch.object(worker, "_run_pipeline_subprocess", return_value=(0, json.dumps(mock_summary), "", mock_summary)), \
         patch("transport.processing_worker.Laptop2ResultPublisher") as MockPublisher:

        mock_pub_inst = MagicMock()
        mock_pub_inst.publish_result = AsyncMock(return_value={"status": "PUBLISHED", "seq": 110})
        mock_pub_inst.close = AsyncMock()
        MockPublisher.return_value = mock_pub_inst

        res = worker.process_event(parent_event_id=test_env["parent_event_id"])
        assert res["status"] == "PROCESSING_COMPLETE"

        # Assert publisher was initialized with explicit NATS URL, not localhost
        MockPublisher.assert_called_once()
        _, kwargs = MockPublisher.call_args
        assert kwargs.get("nats_url") == explicit_nats_url
        assert kwargs.get("nats_url") != "nats://127.0.0.1:4222"
