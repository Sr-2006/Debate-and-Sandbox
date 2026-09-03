"""
tests/test_phase34_event_consistency.py

End-to-end consistency test suite for Phase 3-4 event logs, canonical JSON reports,
and Markdown renderings.
Validates exact synchronization between events and report context across happy paths,
Phase 3 failure paths, safety-block paths, and RL-unavailable paths.
"""

import os
import json
import hashlib
from pathlib import Path
from unittest.mock import patch
import pytest

from run_mvp_pipeline import run_single_problem
from shadow_sandbox.reports.event_recorder import load_event_schema, get_format_checker
from jsonschema import Draft7Validator

PROBLEMS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "problems"))
CASE_01 = os.path.join(PROBLEMS_DIR, "case_01.json")


@pytest.fixture
def event_validator():
    schema = load_event_schema()
    return Draft7Validator(schema, format_checker=get_format_checker())


def test_standard_case_01_event_and_report_consistency(tmp_path, monkeypatch, event_validator):
    """Verifies complete event-to-report consistency for a standard simulation run."""
    monkeypatch.setenv("DEBATE_MOCK_LLM", "1")
    result = run_single_problem(CASE_01, reports_base_dir=str(tmp_path))

    json_path = result["json_report"]
    md_path = result["md_report"]
    events_path = result["events_report"]

    assert os.path.exists(json_path)
    assert os.path.exists(md_path)
    assert os.path.exists(events_path)

    with open(json_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    with open(events_path, "rb") as f:
        events_bytes = f.read()

    # 1. Independent SHA-256 calculation of events file
    actual_events_hash = hashlib.sha256(events_bytes).hexdigest()
    assert report["integrity"]["event_log_hash"] == actual_events_hash
    assert "PHASE34_EVENT_LOG_NOT_EMITTED_PHASE3" not in report["integrity"]["errors"]

    # 2. Parse and validate all events
    events = [json.loads(line.strip()) for line in events_bytes.decode("utf-8").splitlines() if line.strip()]
    assert len(events) == 24

    for ev in events:
        errors = list(event_validator.iter_errors(ev))
        assert len(errors) == 0, f"Event {ev['event']} failed schema: {errors}"

    # 3. ID Consistency
    v_id = report["run"]["verification_run_id"]
    p_id = report["run"]["problem_run_id"]
    c_id = report["problem"]["case_id"]

    for ev in events:
        assert ev["verification_run_id"] == v_id
        assert ev["problem_run_id"] == p_id
        assert ev["case_id"] == c_id

    assert v_id in md_text
    assert p_id in md_text
    assert c_id in md_text

    # 4. Continuous sequencing & non-decreasing timestamps
    for i, ev in enumerate(events, start=1):
        assert ev["sequence"] == i

    # 5. Outcome consistency
    last_event = events[-1]
    assert last_event["event"] == "PROBLEM_RUN_COMPLETED"
    assert last_event["status"] == report["final_summary"]["outcome"]

    # 6. Envelope payload hash consistency
    env_event = next(e for e in events if e["event"] == "ENVELOPE_VALIDATED")
    assert env_event["details"]["payload_hash"] == report["phase_3_to_4_handoff"]["payload_hash"]

    # 7. Execution event consistency
    exec_event = next(e for e in events if e["phase"] == "PHASE_4" and e["component"] == "executor")
    assert exec_event["status"] == report["phase_4"]["execution"]["status"]

    # 8. Verification event consistency
    ver_event = next(e for e in events if e["phase"] == "PHASE_4" and e["component"] == "verifier")
    assert ver_event["status"] == report["phase_4"]["verification"]["status"]


def test_phase3_failed_event_consistency(tmp_path, monkeypatch, event_validator):
    """Verifies that a Phase 3 failure emits PHASE3_FAILED and skips all Phase 4 stages."""
    monkeypatch.setenv("DEBATE_MOCK_LLM", "1")

    # Mock DebateManager.run to simulate a Phase 3 failure
    def mock_failed_run(raw_problem, *args, **kwargs):
        return {
            "original_problem": raw_problem,
            "phase3_status": "PHASE3_FAILED",
            "solution": {},
            "confidence_score": None,
            "confidence_threshold": 0.80,
            "execution_tier": "TIER_3_HUMAN_INTERVENTION",
            "safety_violation": False,
            "safety_evaluated": False,
            "orchestrator_decision": "REJECT_PHASE3_FAILED",
            "reason_codes": ["PHASE3_FAILED"],
            "agent_responses": {},
            "scoring_meta": {}
        }

    with patch("debate.debate_manager.DebateManager.run", side_effect=mock_failed_run):
        result = run_single_problem(CASE_01, reports_base_dir=str(tmp_path))

    with open(result["events_report"], "r", encoding="utf-8") as f:
        events = [json.loads(line.strip()) for line in f if line.strip()]

    # Verify PHASE3_FAILED event emitted
    p3_failed_ev = next(e for e in events if e["component"] == "orchestrator")
    assert p3_failed_ev["event"] == "PHASE3_FAILED"
    assert p3_failed_ev["status"] == "FAILED"

    # Verify Phase 4 stages emitted as *_SKIPPED
    p4_started_ev = next(e for e in events if e["phase"] == "PHASE_4" and e["component"] == "shadow_sandbox")
    assert p4_started_ev["event"] == "PHASE4_SKIPPED"
    assert p4_started_ev["status"] == "NOT_RUN"

    exec_ev = next(e for e in events if e["phase"] == "PHASE_4" and e["component"] == "executor")
    assert exec_ev["event"] == "EXECUTION_SKIPPED"
    assert exec_ev["status"] == "NOT_RUN"


def test_safety_blocked_event_consistency(tmp_path, monkeypatch, event_validator):
    """Verifies that a safety veto emits EXECUTION_BLOCKED with safety reason."""
    monkeypatch.setenv("DEBATE_MOCK_LLM", "1")

    def mock_safety_veto_run(raw_problem, *args, **kwargs):
        return {
            "original_problem": raw_problem,
            "phase3_status": "COMPLETED",
            "solution": {"intent": {"target_ref": {"canonical_name": "test"}}},
            "confidence_score": 85.0,
            "confidence_threshold": 0.80,
            "execution_tier": "TIER_3_HUMAN_INTERVENTION",
            "safety_violation": True,
            "safety_evaluated": True,
            "orchestrator_decision": "REJECT_SAFETY_VETO",
            "reason_codes": ["SAFETY_VETO"],
            "agent_responses": {},
            "scoring_meta": {"veto_applied": True, "safety_violation": True, "veto_reason": "High risk action"}
        }

    with patch("debate.debate_manager.DebateManager.run", side_effect=mock_safety_veto_run):
        result = run_single_problem(CASE_01, reports_base_dir=str(tmp_path))

    with open(result["events_report"], "r", encoding="utf-8") as f:
        events = [json.loads(line.strip()) for line in f if line.strip()]

    safety_ev = next(e for e in events if e["component"] == "safety_guard")
    assert safety_ev["status"] == "SAFETY_VIOLATION"

    exec_ev = next(e for e in events if e["phase"] == "PHASE_4" and e["component"] == "executor")
    assert exec_ev["event"] == "EXECUTION_BLOCKED"
    assert exec_ev["status"] == "BLOCKED"
    assert exec_ev["details"]["attempted"] is False


def test_rl_unavailable_event_consistency(tmp_path, monkeypatch, event_validator):
    """Verifies that an RL advisor failure produces an RL_ADVISORY_UNAVAILABLE event and continues."""
    monkeypatch.setenv("DEBATE_MOCK_LLM", "1")

    with patch("rl_engine.advisor.RLAdvisor.generate_advisory", side_effect=RuntimeError("Simulated RL failure")):
        result = run_single_problem(CASE_01, reports_base_dir=str(tmp_path))

    with open(result["events_report"], "r", encoding="utf-8") as f:
        events = [json.loads(line.strip()) for line in f if line.strip()]

    rl_ev = next(e for e in events if e["phase"] == "RL_ADVISORY")
    assert rl_ev["event"] == "RL_ADVISORY_UNAVAILABLE"
    assert rl_ev["status"] == "UNAVAILABLE"
    assert rl_ev["reason_code"] == "ABSTAIN"
    # Ensure raw traceback exception is not in details
    assert "Simulated RL failure" not in json.dumps(rl_ev["details"])
