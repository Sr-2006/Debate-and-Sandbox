"""Tests for controlled input staging."""

import copy
import json
import os
import tempfile
import pytest

from transport.contracts import CANONICAL_BLOCKS
from transport.nats_receiver import stage_payload_atomically


@pytest.fixture
def sample_payload():
    return {
        "system_context": {"objective": "test"},
        "incident_event": {"incident_id": "case_stage_test", "severity": "HIGH"},
        "infrastructure_topology": {"role": "worker"},
        "service_health_status": {"health": "degraded"},
        "telemetry_evidence": {"log_samples": []},
        "injected_chaos_context": {"active_mutations": "none"}
    }


def test_staged_file_contains_only_six_canonical_blocks(sample_payload):
    """Verify staged input file contains ONLY the six canonical blocks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Add extraneous non-canonical keys to payload
        dirty_payload = copy.deepcopy(sample_payload)
        dirty_payload["event_id"] = "SHOULD_NOT_BE_STAGED"
        dirty_payload["transport_meta"] = {"some": "data"}

        staged_path = stage_payload_atomically(dirty_payload, "evt_staging_01", input_dir=tmpdir)
        assert os.path.exists(staged_path)

        with open(staged_path, "r", encoding="utf-8") as f:
            staged_data = json.load(f)

        assert set(staged_data.keys()) == set(CANONICAL_BLOCKS)
        assert "event_id" not in staged_data
        assert "transport_meta" not in staged_data
        assert staged_data["incident_event"]["incident_id"] == "case_stage_test"


def test_incoming_payload_unmodified(sample_payload):
    """Verify stage_payload_atomically does not mutate the passed payload dict."""
    payload_copy = copy.deepcopy(sample_payload)
    with tempfile.TemporaryDirectory() as tmpdir:
        stage_payload_atomically(sample_payload, "evt_immut", input_dir=tmpdir)
    assert sample_payload == payload_copy


def test_atomic_write_creates_valid_file(sample_payload):
    """Verify atomic write creates the file cleanly without leftover temp files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        staged_path = stage_payload_atomically(sample_payload, "evt_clean", input_dir=tmpdir)
        assert os.path.isfile(staged_path)
        assert not os.path.exists(staged_path + ".tmp")
