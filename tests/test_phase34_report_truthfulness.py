import os
import json
import copy
import pytest
from pathlib import Path
from unittest.mock import patch

import shadow_sandbox.reports as reports_pkg
from shadow_sandbox.reports.report_generator import (
    generate_phase34_report,
    compute_report_hash,
    ReportContractError,
    EMPTY_EVENT_LOG_HASH,
)
from run_mvp_pipeline import (
    _build_phase3_section,
    _build_handoff_section,
    _build_rl_advisory_section,
    _build_phase4_section,
    _build_learning_section,
    _build_final_summary,
)
from debate.debate_manager import DebateManager


@pytest.fixture
def mock_debate_manager():
    return DebateManager()


def test_legacy_producers_are_absent_from_reports_all():
    """Task 9 / Task 8: Legacy producers must not be in reports.__all__."""
    assert "generate_phase34_report" in reports_pkg.__all__
    assert "ReportContractError" in reports_pkg.__all__
    assert "generate_report" not in reports_pkg.__all__
    assert "generate_mvp_report" not in reports_pkg.__all__


def test_missing_phase3_agreement_is_not_reported_as_1_0(mock_debate_manager):
    """Missing agreement must be reported as None/null, not fabricated 1.0."""
    p3_res = {
        "phase3_status": "COMPLETED",
        "confidence_score": 85.0,
        "r1_detailed": {},
        "orchestrator_decision": "APPROVE"
    }
    sol = {"confidence": 0.85}

    p3_sec = _build_phase3_section(p3_res, mock_debate_manager, sol)

    assert p3_sec["agreement"] is None
    assert p3_sec["confidence"]["component_agreement"] == 0.0


def test_missing_safety_result_is_not_reported_as_pass(mock_debate_manager):
    """Failed/incomplete debate without safety check must not be reported as PASS."""
    p3_res = {
        "phase3_status": "PHASE3_FAILED",
        "confidence_score": 0.0,
        "r1_detailed": {}
    }
    sol = {}

    p3_sec = _build_phase3_section(p3_res, mock_debate_manager, sol)

    assert p3_sec["safety"]["status"] != "PASS"
    assert p3_sec["safety"]["status"] in ["UNAVAILABLE", "FAILED"]


def test_safety_violation_is_never_reported_as_approve(mock_debate_manager):
    """A safety violation must report REJECT_SAFETY_VETO, never APPROVE."""
    p3_res = {
        "phase3_status": "COMPLETED",
        "safety_violation": True,
        "scoring_meta": {"veto_applied": True, "veto_reason": "Destructive command detected"},
        "orchestrator_decision": "APPROVE"
    }
    sol = {"confidence": 0.50}

    p3_sec = _build_phase3_section(p3_res, mock_debate_manager, sol)

    assert p3_sec["orchestrator_decision"] == "REJECT_SAFETY_VETO"
    assert p3_sec["safety"]["status"] == "SAFETY_VIOLATION"


def test_attempted_false_remains_false():
    """Stage with attempted=false must remain false across normalization."""
    p4_raw = {
        "status": "NOT_RUN",
        "attestation": {"attempted": False, "status": "NOT_RUN", "reason": "Blocked"},
        "execution": {"attempted": False, "capability": "NOT_RUN", "mode": "OBSERVE", "parameters": {}, "result": None}
    }

    p4_sec = _build_phase4_section(p4_raw)

    assert p4_sec["attestation"]["attempted"] is False
    assert p4_sec["execution"]["attempted"] is False


def test_passed_false_never_becomes_completed():
    """Attestation/verification with passed=false must be FAILED, never COMPLETED/PASS."""
    p4_raw = {
        "status": "ATTESTATION_FAILED",
        "attestation": {"attested": False, "reason": "Label missing"},
        "verification": {"passed": False, "reason": "Guardrail failed"}
    }

    p4_sec = _build_phase4_section(p4_raw)

    assert p4_sec["attestation"]["status"] == "FAILED"
    assert p4_sec["verification"]["status"] == "FAILED"


def test_failed_real_shadow_execution_is_not_learning_eligible():
    """Failed real-shadow execution must be eligible=false with null reward."""
    p4_sec = _build_phase4_section({
        "status": "SANDBOX_FAILED_ROLLED_BACK",
        "execution": {"attempted": True, "status": "FAILED", "capability": "container.restart", "mode": "MUTATE_REVERSIBLE", "parameters": {}, "result": {"success": False}},
        "attestation": {"attested": True, "attempted": True},
        "verification": {"passed": False, "attempted": True}
    })

    learning_sec = _build_learning_section(None, is_simulated=False, p4_sec=p4_sec)

    assert learning_sec["eligible"] is False
    assert learning_sec["reward"] is None


def test_failed_verification_cannot_receive_reward_1_0():
    """Failed verification must never receive positive reward 1.0."""
    p4_sec = _build_phase4_section({
        "status": "SANDBOX_FAILED_ROLLED_BACK",
        "execution": {"attempted": True, "status": "SUCCESS", "capability": "container.restart", "mode": "MUTATE_REVERSIBLE", "parameters": {}, "result": {"success": True}},
        "attestation": {"attested": True, "attempted": True},
        "verification": {"passed": False, "attempted": True},
        "rollback": {"attempted": True, "result": "FAILED", "status": "FAILED"}
    })

    learning_sec = _build_learning_section(None, is_simulated=False, p4_sec=p4_sec)

    assert learning_sec["eligible"] is False
    assert learning_sec["reward"] is None or learning_sec["reward"] <= 0.0


def test_absent_rl_advisory_becomes_abstain_unavailable():
    """Absent RL advisor must yield status=UNAVAILABLE and recommendation=ABSTAIN."""
    rl_sec = _build_rl_advisory_section(None)

    assert rl_sec["status"] == "UNAVAILABLE"
    assert rl_sec["recommendation"] == "ABSTAIN"
    assert rl_sec["influence_allowed"] is False
    assert "RL_ADVISOR_UNAVAILABLE" in rl_sec["reason_codes"]


def test_schema_valid_unknown_intent_has_capability_mapped_false():
    """Schema-valid envelope with unknown capability must have capability_mapped=false."""
    envelope = {
        "schema_version": "2.0",
        "event_id": "evt_unk",
        "event_type": "autosre.action.proposed",
        "incident_id": "case_unknown",
        "correlation_id": "corr_unk",
        "fingerprint": "fp_unk",
        "created_at": "2026-09-03T12:00:00Z",
        "source": {"phase": "PHASE_3", "code_commit": "2f2127d7"},
        "problem_summary": "Unknown capability test",
        "target_ref": {"kind": "container", "canonical_name": "unknown-svc"},
        "phase3_confidence": {"score": 0.80},
        "execution_tier": "TIER_1_AUTONOMOUS_EXECUTION",
        "safety_violation": False,
        "evidence_refs": ["log_1"],
        "intents": [
            {
                "intent_id": "int_unk",
                "intent_type": "unknown.fake.capability",  # Not in capabilities.yaml
                "mode": "MUTATE_REVERSIBLE",
                "target_ref": {"kind": "container", "canonical_name": "unknown-svc"},
                "parameters": {},
                "evidence_refs": ["log_1"],
                "preconditions": [],
                "postconditions": [],
                "timeout_seconds": 30,
                "max_attempts": 1,
                "risk_class": "MEDIUM",
                "requires_human_approval": False
            }
        ],
        "human_summary": "Unknown test"
    }

    handoff = _build_handoff_section(envelope, is_valid=True, errs=[])

    assert handoff["schema_valid"] is True
    assert handoff["capability_mapped"] is False
    assert handoff["mvp_supported"] is False


def test_phase3_confidence_and_threshold_preserved_exactly(mock_debate_manager):
    """Deterministic score and threshold must be preserved exactly."""
    p3_res = {
        "phase3_status": "COMPLETED",
        "confidence_score": 77.0,
        "confidence_threshold": 0.85,
        "r1_detailed": {}
    }
    sol = {"confidence": 0.77}

    p3_sec = _build_phase3_section(p3_res, mock_debate_manager, sol)

    assert p3_sec["confidence"]["score"] == 0.77
    assert p3_sec["confidence"]["threshold"] == 0.85


def test_simulation_verified_recommends_real_shadow_validation():
    """SIMULATION_VERIFIED must recommend RUN_REAL_SHADOW_VALIDATION."""
    p4_sec = _build_phase4_section({"status": "SIMULATION_VERIFIED", "execution": {"attempted": True, "capability": "container.restart", "mode": "MUTATE_REVERSIBLE"}})
    p3_sec = {"confidence": {"score": 0.90}, "safety": {"status": "PASS"}}

    summary = _build_final_summary(p4_sec, p3_sec, sol={}, is_simulated=True)

    assert summary["recommended_next_action"] == "RUN_REAL_SHADOW_VALIDATION"
    assert summary["problem_resolved_in_sandbox"] is False
    assert any("simulation does not prove" in lim for lim in summary["limitations"])


def test_old_report_pairs_survive_write_failures(tmp_path):
    """Existing valid JSON/MD pairs must survive write and replacement failures."""
    context = {
        "schema_version": "phase34-report-v1",
        "report_type": "PHASE34_PROBLEM_SUMMARY",
        "run": {
            "verification_run_id": "verify_survive123",
            "problem_run_id": "run_survive123",
            "commit_sha": "f7765376888faaf3f0c6f44600463857660eb21a",
            "started_at": "2026-09-03T12:00:00Z",
            "completed_at": "2026-09-03T12:00:05Z",
            "duration_ms": 5000,
            "execution_mode": "SIMULATION",
            "mock_llm": True,
            "rl_operating_mode": "SHADOW",
            "laptop1_transport": "disabled"
        },
        "problem": {
            "case_id": "case_01",
            "source_file": "problems/case_01.json",
            "input_hash": "a" * 64,
            "raw_input": {"incident_id": "case_01"},
            "normalized_incident": {"type": "disk_pressure"},
            "severity": "HIGH",
            "target": {"kind": "container", "canonical_name": "postgres-db"},
            "expected_behavior": "System operates normally"
        },
        "phase_3": {
            "status": "COMPLETED",
            "started_at": "2026-09-03T12:00:00Z",
            "completed_at": "2026-09-03T12:00:02Z",
            "duration_ms": 2000,
            "agents": {
                "optimist": {"name": "optimist", "status": "SUCCESS", "prompt": "p", "raw_response": "r", "parsed_response": {}, "valid": True, "latency_ms": 100, "error": None},
                "critic": {"name": "critic", "status": "SUCCESS", "prompt": "p", "raw_response": "r", "parsed_response": {}, "valid": True, "latency_ms": 100, "error": None},
                "fact_checker": {"name": "fact_checker", "status": "SUCCESS", "prompt": "p", "raw_response": "r", "parsed_response": {}, "valid": True, "latency_ms": 100, "error": None}
            },
            "agreement": 0.9,
            "confidence": {
                "score": 0.90, "threshold": 0.80, "uncertainty": 0.10, "calibration_status": "CALIBRATED",
                "evidence_count": 1, "component_agreement": 0.9, "evidence_grounding": 0.9, "veto_applied": False,
                "veto_cap": None, "reason_codes": ["DIAGNOSED"]
            },
            "safety": {"status": "PASS"},
            "selected_intent": {"intent_type": "container.restart"},
            "orchestrator_decision": "APPROVE",
            "reason_codes": ["DIAGNOSED"]
        },
        "phase_3_to_4_handoff": {
            "status": "SUCCESS", "schema_valid": True, "validation_errors": [], "payload_hash": "b" * 64,
            "exact_envelope": {"schema_version": "2.0"}, "capability_mapped": True, "mvp_supported": True, "target_resolved": True
        },
        "rl_advisory": {
            "status": "SUCCESS", "operating_mode": "SHADOW", "policy_version": "v1.0", "model_version": "v1.0",
            "recommendation": "ACCEPT_PROPOSAL", "allowed_actions": ["ACCEPT_PROPOSAL"], "action_scores": {"ACCEPT_PROPOSAL": 0.9},
            "uncertainty": 0.1, "sample_size": 5, "cold_start": False, "influence_allowed": False, "reason_codes": [],
            "feature_hash": "c" * 64, "latency_ms": 10
        },
        "phase_4": {
            "status": "SIMULATION_VERIFIED", "started_at": "2026-09-03T12:00:03Z", "completed_at": "2026-09-03T12:00:05Z",
            "duration_ms": 2000, "exact_input": {"schema_version": "2.0"}, "target": {"kind": "container", "canonical_name": "postgres-db"},
            "attestation": {"status": "PASSED", "attempted": True, "reason_code": "DIAGNOSED", "reason": "Attested", "data": {}, "duration_ms": 5},
            "before_observations": {"status": "COMPLETED", "attempted": True, "reason_code": "DIAGNOSED", "reason": "Observed", "data": {}, "duration_ms": 5},
            "fault_setup": {"status": "COMPLETED", "attempted": True, "reason_code": "DIAGNOSED", "reason": "Faulted", "data": {}, "duration_ms": 5},
            "execution": {"status": "SUCCESS", "attempted": True, "capability": "container.restart", "mode": "MUTATE_REVERSIBLE", "parameters": {}, "result": {"success": True}, "duration_ms": 50},
            "after_observations": {"status": "COMPLETED", "attempted": True, "reason_code": "DIAGNOSED", "reason": "Observed", "data": {}, "duration_ms": 5},
            "verification": {"status": "PASSED", "attempted": True, "reason_code": "VERIFIED_RECOVERED", "reason": "Verified", "data": {}, "duration_ms": 5},
            "rollback": {"status": "NOT_RUN", "attempted": False, "reason_code": "NOT_RUN", "reason": "Not run", "data": {}, "duration_ms": 0},
            "cleanup": {"status": "COMPLETED", "attempted": True, "reason_code": "DIAGNOSED", "reason": "Cleaned", "data": {}, "duration_ms": 5},
            "state_history": ["REPORTED"], "reason_codes": ["VERIFIED_RECOVERED"]
        },
        "learning": {
            "status": "NOT_ELIGIBLE", "episode_id": "ep_survive123", "eligible": False, "eligibility_reason": "SIMULATION_MODE",
            "behavior_action": "ACCEPT_PROPOSAL", "reward": None, "sample_weight": 0.0, "feature_hash": "c" * 64, "stored": True
        },
        "final_summary": {
            "outcome": "SIMULATION_VERIFIED", "problem_resolved_in_sandbox": False, "execution_performed": True,
            "human_intervention_required": False, "recommended_next_action": "RUN_REAL_SHADOW_VALIDATION",
            "what_happened": "Verified", "why_it_happened": "Success", "safety_result": "PASS", "confidence_result": "HIGH",
            "limitations": ["Simulation execution mode active"]
        },
        "integrity": {
            "report_schema_valid": True, "input_hash": "a" * 64, "payload_hash": "b" * 64,
            "event_log_hash": EMPTY_EVENT_LOG_HASH, "report_hash": "0" * 64, "errors": ["PHASE34_EVENT_LOG_NOT_EMITTED_PHASE3"]
        }
    }

    # 1. Create valid original report pair
    j_orig, m_orig = generate_phase34_report(context, reports_base_dir=str(tmp_path))
    assert os.path.exists(j_orig)
    assert os.path.exists(m_orig)

    with open(j_orig, "r", encoding="utf-8") as f:
        orig_content = f.read()

    # 2. Inject failure on second replacement call (moving tmp_md to dest_md)
    original_replace = os.replace
    replacement_count = 0

    def failing_replace(src, dst):
        nonlocal replacement_count
        if ".phase34_report.md.tmp." in src:
            raise OSError("Injected replacement failure for Markdown")
        return original_replace(src, dst)

    with patch("os.replace", side_effect=failing_replace):
        with pytest.raises(OSError, match="Injected replacement failure"):
            generate_phase34_report(context, reports_base_dir=str(tmp_path))

    # Assert original pair survived
    assert os.path.exists(j_orig)
    assert os.path.exists(m_orig)
    with open(j_orig, "r", encoding="utf-8") as f:
        assert f.read() == orig_content

    # Assert zero temporary or backup files remained
    dest_dir = Path(j_orig).parent
    remaining_files = [p.name for p in dest_dir.iterdir() if p.name.startswith(".phase34_report")]
    assert len(remaining_files) == 0, f"Temporary/backup files leaked: {remaining_files}"
