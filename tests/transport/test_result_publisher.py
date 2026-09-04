"""Unit tests for Laptop2ResultPublisher, traceability, and semantic deduplication."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from transport.result_publisher import (
    Laptop2ResultPublisher,
    build_phase34_completed_event
)
from transport.dedup_store import DedupStore


def make_valid_event(
    event_id="evt_11111111111111111111111111111111",
    parent_event_id="evt_22222222222222222222222222222222",
    correlation_id="corr_33333333333333333333333333333333",
    incident_id="order-service_51",
    report_hash="a" * 64,
    event_log_hash="b" * 64,
    input_payload_sha256="c" * 64
):
    return {
        "schema_version": "v1",
        "event_id": event_id,
        "event_type": "autosre.phase34.completed",
        "incident_id": incident_id,
        "parent_event_id": parent_event_id,
        "correlation_id": correlation_id,
        "created_at": "2026-09-04T08:00:00Z",
        "source": {
            "engine": "laptop2",
            "git_sha": "dc1f7f011c7e8a6c8384058c4c61290793b1a5f0",
            "generated_at": "2026-09-04T08:00:00Z"
        },
        "final_outcome": "SANDBOX_VERIFIED",
        "phase_3": {
            "status": "COMPLETED",
            "confidence_score": 0.9,
            "orchestrator_decision": "TIER_1_AUTONOMOUS_EXECUTION",
            "safety_status": "PASS",
            "selected_intent": None
        },
        "rl_advisory": {
            "status": "SUCCESS",
            "operating_mode": "SHADOW",
            "advisory_decision": "ALLOW",
            "advisory_confidence": 0.88,
            "feature_schema_version": "features-v2",
            "feature_hash": "e96bc03c2bb6f0b7e42d7dae6abac3f295b9d3fa6e469eefb0ca78bc9cfecce0"
        },
        "phase_4": {
            "status": "SANDBOX_VERIFIED",
            "target": {"kind": "database", "canonical_name": "postgres-db"},
            "attestation_status": "PASSED",
            "execution_status": "SUCCESS",
            "execution_capability": "postgres.setting.update",
            "verification_status": "PASSED",
            "rollback_status": "NOT_RUN"
        },
        "integrity": {
            "input_payload_sha256": input_payload_sha256,
            "report_hash": report_hash,
            "events_log_hash": event_log_hash
        }
    }


def test_result_publisher_publishes_and_records(tmp_path):
    """Verify publisher publishes payload over JetStream and records delivery in SQLite."""
    async def _test():
        db_path = str(tmp_path / "test_transport.db")
        publisher = Laptop2ResultPublisher(state_db_path=db_path)
        sample_event = make_valid_event()

        mock_js = AsyncMock()
        mock_ack = MagicMock()
        mock_ack.stream = "AUTOSRE"
        mock_ack.seq = 42
        mock_js.publish = AsyncMock(return_value=mock_ack)
        publisher.js = mock_js

        res = await publisher.publish_result(sample_event, report_path="reports/sample.json")
        assert res["status"] == "PUBLISHED"
        assert res["seq"] == 42
        assert res["event_id"] == sample_event["event_id"]

        # Verify SQLite record in published_results table
        dedup = DedupStore(db_path)
        record = dedup.get_published(sample_event["event_id"])
        assert record is not None
        assert record["parent_event_id"] == sample_event["parent_event_id"]
        assert record["correlation_id"] == sample_event["correlation_id"]
        assert record["final_outcome"] == "SANDBOX_VERIFIED"
        assert record["stream_seq"] == 42
        assert record["report_hash"] == sample_event["integrity"]["report_hash"]

    asyncio.run(_test())


def test_restart_semantic_dedup_skips_publication(tmp_path):
    """Verify that restarting with the same parent_event_id and report_hash skips publishing."""
    async def _test():
        db_path = str(tmp_path / "test_transport.db")
        publisher = Laptop2ResultPublisher(state_db_path=db_path)

        event_1 = make_valid_event(event_id="evt_11111111111111111111111111111111", report_hash="a" * 64)

        mock_js = AsyncMock()
        mock_ack = MagicMock()
        mock_ack.stream = "AUTOSRE"
        mock_ack.seq = 100
        mock_js.publish = AsyncMock(return_value=mock_ack)
        publisher.js = mock_js

        # First publish
        res_1 = await publisher.publish_result(event_1)
        assert res_1["status"] == "PUBLISHED"
        assert mock_js.publish.call_count == 1

        # Second publish attempt with new event_id but identical parent + report_hash
        event_2 = make_valid_event(event_id="evt_22222222222222222222222222222222", report_hash="a" * 64)
        res_2 = await publisher.publish_result(event_2)

        assert res_2["status"] == "SKIPPED_ALREADY_PUBLISHED"
        assert res_2["event_id"] == "evt_11111111111111111111111111111111"
        assert mock_js.publish.call_count == 1  # No additional NATS publish

    asyncio.run(_test())


def test_changed_report_hash_permits_new_result(tmp_path):
    """Verify that a changed report hash for the same parent event permits publishing a new result."""
    async def _test():
        db_path = str(tmp_path / "test_transport.db")
        publisher = Laptop2ResultPublisher(state_db_path=db_path)

        event_1 = make_valid_event(event_id="evt_11111111111111111111111111111111", report_hash="a" * 64)

        mock_js = AsyncMock()
        mock_ack_1 = MagicMock()
        mock_ack_1.stream = "AUTOSRE"
        mock_ack_1.seq = 101

        mock_ack_2 = MagicMock()
        mock_ack_2.stream = "AUTOSRE"
        mock_ack_2.seq = 102

        mock_js.publish = AsyncMock(side_effect=[mock_ack_1, mock_ack_2])
        publisher.js = mock_js

        # First publish
        res_1 = await publisher.publish_result(event_1)
        assert res_1["status"] == "PUBLISHED"
        assert res_1["seq"] == 101

        # Second publish with changed report hash
        event_2 = make_valid_event(event_id="evt_22222222222222222222222222222222", report_hash="f" * 64)
        res_2 = await publisher.publish_result(event_2)

        assert res_2["status"] == "PUBLISHED"
        assert res_2["seq"] == 102
        assert mock_js.publish.call_count == 2

    asyncio.run(_test())


def test_ambiguous_incident_history_traceability(tmp_path):
    """Verify that when multiple events exist for an incident, specific parent_event_id resolution is exact."""
    db_path = str(tmp_path / "test_transport.db")
    dedup = DedupStore(db_path)

    # Insert two events for same incident
    dedup.record_received(
        event_id="evt_version1_1111111111111111111111",
        incident_id="order-service_51",
        payload_hash="1" * 64,
        correlation_id="corr_v1_1111111111111111111111111"
    )
    dedup.record_received(
        event_id="evt_version2_2222222222222222222222",
        incident_id="order-service_51",
        payload_hash="2" * 64,
        correlation_id="corr_v2_2222222222222222222222222"
    )

    # Querying specific event_id returns exact matching correlation and payload_hash
    row1 = dedup.get_event("evt_version1_1111111111111111111111")
    assert row1["correlation_id"] == "corr_v1_1111111111111111111111111"
    assert row1["payload_hash"] == "1" * 64

    row2 = dedup.get_event("evt_version2_2222222222222222222222")
    assert row2["correlation_id"] == "corr_v2_2222222222222222222222222"
    assert row2["payload_hash"] == "2" * 64
