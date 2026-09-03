"""
tests/test_phase34_event_recorder.py

Unit and failure-injection test suite for Phase34EventRecorder.
Tests sequencing invariants, timestamp invariants, deterministic serialization,
atomic file writes, temporary-file cleanup, backup preservation, and error handling.
"""

import os
import json
import uuid
import hashlib
from pathlib import Path
from unittest.mock import patch
import pytest

from shadow_sandbox.reports.event_recorder import (
    Phase34EventRecorder,
    EventContractError,
    EventWriteError,
)


def _populate_sample_events(recorder: Phase34EventRecorder) -> None:
    recorder.record(
        phase="INPUT",
        component="coordinator",
        event="PROBLEM_RECEIVED",
        status="COMPLETED",
        reason_code="PROBLEM_LOADED",
        duration_ms=0.0,
        details={"case_id": recorder.case_id}
    )
    recorder.record(
        phase="PHASE_3",
        component="debate_manager",
        event="PHASE3_STARTED",
        status="STARTED",
        reason_code="STARTED",
        duration_ms=0.0,
        details={}
    )
    recorder.record(
        phase="PHASE_3",
        component="agent.optimist",
        event="OPTIMIST_COMPLETED",
        status="SUCCESS",
        reason_code="SUCCESS",
        duration_ms=50.0,
        details={"valid": True}
    )


def test_continuous_sequences():
    rec = Phase34EventRecorder("verify_seq", "run_seq", "case_seq")
    _populate_sample_events(rec)

    assert len(rec.events) == 3
    assert [ev["sequence"] for ev in rec.events] == [1, 2, 3]


def test_non_decreasing_timestamps():
    rec = Phase34EventRecorder("verify_ts", "run_ts", "case_ts")
    rec.record("INPUT", "coordinator", "PROBLEM_RECEIVED", "COMPLETED", timestamp="2026-09-03T12:00:00Z")
    rec.record("PHASE_3", "debate_manager", "PHASE3_STARTED", "STARTED", timestamp="2026-09-03T12:00:01Z")

    # Attempting to record an earlier timestamp must raise EventContractError
    with pytest.raises(EventContractError, match="Timestamp violation"):
        rec.record("PHASE_3", "agent.optimist", "OPTIMIST_COMPLETED", "SUCCESS", timestamp="2026-09-03T11:59:59Z")


def test_deterministic_serialization_and_final_newline():
    rec1 = Phase34EventRecorder("verify_det", "run_det", "case_det")
    _populate_sample_events(rec1)

    serialized1 = rec1.serialize_jsonl()
    assert serialized1.endswith("\n"), "Serialized JSONL must end with a newline"

    lines = serialized1.strip().split("\n")
    assert len(lines) == 3

    # Ensure keys are sorted compactly in each line
    for line in lines:
        parsed = json.loads(line)
        expected = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert line == expected

    # Identical events should produce identical hash
    rec2 = Phase34EventRecorder("verify_det", "run_det", "case_det")
    for ev in rec1.events:
        rec2.record(
            phase=ev["phase"],
            component=ev["component"],
            event=ev["event"],
            status=ev["status"],
            reason_code=ev["reason_code"],
            duration_ms=ev["duration_ms"],
            details=ev["details"],
            timestamp=ev["timestamp"]
        )

    assert rec1.compute_hash() == rec2.compute_hash()
    assert rec1.compute_hash() == hashlib.sha256(serialized1.encode("utf-8")).hexdigest()


def test_atomic_write_creates_valid_jsonl(tmp_path):
    rec = Phase34EventRecorder("verify_atomic", "run_atomic", "case_01")
    _populate_sample_events(rec)

    dest_path = rec.write_atomic(reports_base_dir=str(tmp_path))
    assert os.path.exists(dest_path)
    assert dest_path.endswith("phase34_events.jsonl")

    # Read back and verify lines
    with open(dest_path, "r", encoding="utf-8") as f:
        read_lines = [json.loads(line.strip()) for line in f if line.strip()]

    assert len(read_lines) == 3
    assert [ev["sequence"] for ev in read_lines] == [1, 2, 3]


def test_atomic_write_cleans_temporary_files_on_success(tmp_path):
    rec = Phase34EventRecorder("verify_clean", "run_clean", "case_01")
    _populate_sample_events(rec)

    dest_path = rec.write_atomic(reports_base_dir=str(tmp_path))
    case_dir = Path(dest_path).parent

    tmp_files = [p.name for p in case_dir.iterdir() if ".tmp." in p.name]
    bak_files = [p.name for p in case_dir.iterdir() if ".bak." in p.name]
    assert len(tmp_files) == 0, f"No tmp files should remain: {tmp_files}"
    assert len(bak_files) == 0, f"No bak files should remain: {bak_files}"


def test_atomic_write_failure_cleans_tmp_and_preserves_earlier_log(tmp_path):
    rec1 = Phase34EventRecorder("verify_preserve", "run_preserve", "case_01")
    _populate_sample_events(rec1)
    orig_path = rec1.write_atomic(reports_base_dir=str(tmp_path))

    with open(orig_path, "r", encoding="utf-8") as f:
        orig_content = f.read()

    # Create a second recorder with updated events
    rec2 = Phase34EventRecorder("verify_preserve", "run_preserve", "case_01")
    _populate_sample_events(rec2)
    rec2.record("PHASE_4", "shadow_sandbox", "PHASE4_STARTED", "STARTED", duration_ms=0.0)

    orig_replace = os.replace

    def failing_replace(src, dst):
        if ".phase34_events.jsonl.tmp." in src:
            raise OSError("Simulated replace failure")
        return orig_replace(src, dst)

    with patch("os.replace", side_effect=failing_replace):
        with pytest.raises(EventWriteError):
            rec2.write_atomic(reports_base_dir=str(tmp_path))

    case_dir = Path(orig_path).parent
    tmp_files = [p.name for p in case_dir.iterdir() if ".tmp." in p.name]
    assert len(tmp_files) == 0, f"Tmp files must be cleaned up on failure: {tmp_files}"

    # Verify original file content is preserved intact
    assert os.path.exists(orig_path)
    with open(orig_path, "r", encoding="utf-8") as f:
        current_content = f.read()
    assert current_content == orig_content


def test_empty_identifers_rejected():
    with pytest.raises(EventContractError):
        Phase34EventRecorder("", "run", "case")
    with pytest.raises(EventContractError):
        Phase34EventRecorder("verif", "", "case")
    with pytest.raises(EventContractError):
        Phase34EventRecorder("verif", "run", "")


def test_no_sensitive_data_in_details():
    rec = Phase34EventRecorder("verify_sec", "run_sec", "case_sec")
    ev = rec.record(
        phase="INPUT",
        component="coordinator",
        event="PROBLEM_RECEIVED",
        status="COMPLETED",
        reason_code="PROBLEM_LOADED",
        details={"case_id": "case_sec", "severity": "HIGH"}
    )
    serialized = json.dumps(ev["details"])
    for sensitive in ["password", "token", "secret", "private_key", "/home/", "C:\\Users"]:
        assert sensitive not in serialized.lower()
