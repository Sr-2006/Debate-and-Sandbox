"""Tests for receiver end-to-end deduplication and idempotency."""

import copy
import json
import os
import tempfile
import pytest

from transport.canonical_json import compute_payload_sha256
from transport.nats_receiver import Laptop2IncidentReceiver
from transport.contracts import EventStatus


@pytest.fixture
def test_environment():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "transport.db")
        staging_dir = os.path.join(tmpdir, "staging")
        receiver = Laptop2IncidentReceiver(
            state_db_path=db_path,
            input_dir=staging_dir,
            publish_receipts=False
        )
        yield receiver, db_path, staging_dir


@pytest.fixture
def make_event():
    def _builder(event_id="evt_101", incident_id="case_101", extra_marker="v1"):
        payload = {
            "system_context": {"objective": "RCA", "marker": extra_marker},
            "incident_event": {"incident_id": incident_id, "severity": "HIGH"},
            "infrastructure_topology": {"role": "worker"},
            "service_health_status": {"health": "degraded"},
            "telemetry_evidence": {"log_samples": []},
            "injected_chaos_context": {"active_mutations": "none"}
        }
        payload_hash = compute_payload_sha256(payload)
        return {
            "schema_version": "1.0",
            "event_type": "autosre.incident.ready",
            "event_id": event_id,
            "correlation_id": f"corr_{event_id}",
            "incident_id": incident_id,
            "source": {
                "engine": "laptop1",
                "dataset_version": None,
                "git_sha": None,
                "generated_at": None
            },
            "transport": {"payload_sha256": payload_hash, "sent_at": "2026-09-04T00:00:00Z"},
            "payload": payload
        }
    return _builder


def test_first_event_stages_successfully(test_environment, make_event):
    """Verify first event transitions to STAGED and writes file."""
    receiver, db_path, staging_dir = test_environment
    event = make_event("evt_01", "case_01")

    success, status, staged_path, val_res = receiver.process_event_payload(event)
    assert success is True
    assert status == "STAGED"
    assert staged_path is not None
    assert os.path.isfile(staged_path)

    stored = receiver.dedup_store.get_event("evt_01")
    assert stored["status"] == EventStatus.STAGED.value
    assert stored["input_path"] == staged_path


def test_same_event_id_redelivery_suppressed(test_environment, make_event):
    """Verify CASE A: re-delivering the same event_id is recognized as ALREADY_STAGED and skips re-writing."""
    receiver, db_path, staging_dir = test_environment
    event = make_event("evt_02", "case_02")

    success1, status1, path1, _ = receiver.process_event_payload(event)
    assert status1 == "STAGED"

    # Redeliver identical event
    success2, status2, path2, _ = receiver.process_event_payload(event)
    assert success2 is True
    assert status2 == "ALREADY_STAGED"
    assert path2 == path1


def test_semantic_duplicate_suppressed(test_environment, make_event):
    """Verify CASE C: different event_id with identical incident_id + payload_hash is suppressed."""
    receiver, db_path, staging_dir = test_environment
    event1 = make_event("evt_orig", "case_dup", extra_marker="same")
    event2 = make_event("evt_new_id", "case_dup", extra_marker="same")

    success1, status1, path1, _ = receiver.process_event_payload(event1)
    assert status1 == "STAGED"

    # Second event has different event_id but identical payload hash
    success2, status2, path2, _ = receiver.process_event_payload(event2)
    assert success2 is True
    assert status2 == "SEMANTIC_DUPLICATE_STAGED"
    assert path2 == path1
    assert not os.path.exists(os.path.join(staging_dir, "evt_new_id.json"))


def test_changed_payload_hash_creates_new_staged_case(test_environment, make_event):
    """Verify CASE D: same incident_id but modified payload hash is accepted as new case."""
    receiver, db_path, staging_dir = test_environment
    event_v1 = make_event("evt_v1", "case_evolve", extra_marker="state1")
    event_v2 = make_event("evt_v2", "case_evolve", extra_marker="state2")

    success1, status1, path1, _ = receiver.process_event_payload(event_v1)
    assert status1 == "STAGED"

    success2, status2, path2, _ = receiver.process_event_payload(event_v2)
    assert success2 is True
    assert status2 == "STAGED"
    assert path1 != path2
    assert os.path.exists(path1)
    assert os.path.exists(path2)


def test_invalid_event_marked_failed(test_environment, make_event):
    """Verify invalid event is marked FAILED in DB and not staged."""
    receiver, db_path, staging_dir = test_environment
    event = make_event("evt_bad", "case_bad")
    event["source"]["engine"] = "invalid_engine"

    success, status, path, val_res = receiver.process_event_payload(event)
    assert success is False
    assert status in ["INVALID_SOURCE_ENGINE", "REJECTED_SCHEMA"]
    assert path is None

    stored = receiver.dedup_store.get_event("evt_bad")
    assert stored["status"] == EventStatus.FAILED.value
    assert not os.path.exists(os.path.join(staging_dir, "evt_bad.json"))
