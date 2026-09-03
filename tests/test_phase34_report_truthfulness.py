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
    ReportAtomicityError,
    EMPTY_EVENT_LOG_HASH,
)
from run_mvp_pipeline import (
    _build_phase3_section,
    _build_handoff_section,
    _build_rl_advisory_section,
    _build_phase4_section,
    _build_learning_section,
    _build_final_summary,
    _build_stage_execution,
    _build_stage_cleanup,
    _build_failed_phase4_section,
    run_single_problem,
)
from debate.config import AUTONOMOUS_THRESHOLD
from debate.debate_manager import DebateManager


@pytest.fixture
def mock_debate_manager():
    return DebateManager()


def test_legacy_producers_are_absent_from_reports_all():
    """Task 9 / Task 8: Legacy producers must not be in reports.__all__."""
    assert "generate_phase34_report" in reports_pkg.__all__
    assert "ReportContractError" in reports_pkg.__all__
    assert "ReportAtomicityError" in reports_pkg.__all__
    assert "generate_report" not in reports_pkg.__all__
    assert "generate_mvp_report" not in reports_pkg.__all__


def test_missing_phase3_status_never_becomes_completed(mock_debate_manager):
    """Missing phase3_status must not default to COMPLETED."""
    p3_res = {
        "confidence_score": 85.0,
        "r1_detailed": {}
    }
    sol = {"confidence": 0.85}

    p3_sec = _build_phase3_section(p3_res, mock_debate_manager, sol)

    assert p3_sec["status"] == "PHASE3_FAILED"


def test_missing_deterministic_confidence_never_uses_model_confidence(mock_debate_manager):
    """Missing deterministic confidence score must remain null, never taking model confidence."""
    p3_res = {
        "phase3_status": "COMPLETED",
        "r1_detailed": {}
    }
    sol = {"confidence": 0.95}

    p3_sec = _build_phase3_section(p3_res, mock_debate_manager, sol)

    assert p3_sec["confidence"]["score"] is None
    assert p3_sec["confidence"]["calibration_status"] == "UNAVAILABLE"


def test_threshold_comes_from_debate_config(mock_debate_manager):
    """Confidence threshold must equal AUTONOMOUS_THRESHOLD / 100.0 from debate.config."""
    p3_res = {
        "phase3_status": "COMPLETED",
        "confidence_score": 80.0,
        "r1_detailed": {}
    }
    sol = {}

    p3_sec = _build_phase3_section(p3_res, mock_debate_manager, sol)

    assert p3_sec["confidence"]["threshold"] == float(AUTONOMOUS_THRESHOLD / 100.0)


def test_missing_safety_evaluation_never_becomes_pass(mock_debate_manager):
    """Failed/incomplete debate without safety check must not be reported as PASS."""
    p3_res = {
        "phase3_status": "PHASE3_FAILED",
        "r1_detailed": {}
    }
    sol = {}

    p3_sec = _build_phase3_section(p3_res, mock_debate_manager, sol)

    assert p3_sec["safety"]["status"] == "UNAVAILABLE"


def test_missing_orchestrator_decision_never_becomes_autonomous_execution(mock_debate_manager):
    """Missing orchestrator decision must remain UNAVAILABLE, never defaulting to autonomous execution."""
    p3_res = {
        "phase3_status": "COMPLETED",
        "r1_detailed": {}
    }
    sol = {}

    p3_sec = _build_phase3_section(p3_res, mock_debate_manager, sol)

    assert p3_sec["orchestrator_decision"] == "UNAVAILABLE"


def test_arbitrary_observation_data_never_proves_completion():
    """Observation dictionary without explicit attempted field maps to UNAVAILABLE."""
    p4_raw = {
        "status": "COMPLETED",
        "before_observations": {"data": {"cpu": "99%"}},
        "execution": {"attempted": False}
    }

    p4_sec = _build_phase4_section(p4_raw)

    assert p4_sec["before_observations"]["status"] == "UNAVAILABLE"
    assert p4_sec["before_observations"]["attempted"] is False


def test_execution_without_explicit_result_never_becomes_success():
    """Execution with attempted=true but missing result object maps to UNKNOWN."""
    exec_dict = {"attempted": True, "capability": "container.restart", "mode": "MUTATE_REVERSIBLE"}

    stage = _build_stage_execution(exec_dict)

    assert stage["status"] == "UNKNOWN"
    assert stage["result"] is None


def test_cleanup_without_explicit_attempted_completed_fields_is_unavailable():
    """Cleanup stage without explicit attempted/completed fields maps to UNAVAILABLE."""
    clean_dict = {"data": {"removed": True}}

    stage = _build_stage_cleanup(clean_dict)

    assert stage["status"] == "UNAVAILABLE"
    assert stage["attempted"] is False


def test_phase3_failed_leaves_every_phase4_attempted_flag_false():
    """PHASE3_FAILED must leave every single Phase 4 stage with attempted=False."""
    p4_sec = _build_failed_phase4_section()

    for stage_name in ["attestation", "before_observations", "fault_setup", "execution", "after_observations", "verification", "rollback", "cleanup"]:
        st = p4_sec[stage_name]
        assert st["attempted"] is False, f"Stage {stage_name} attempted should be False"
        assert st["status"] == "NOT_RUN", f"Stage {stage_name} status should be NOT_RUN"
        assert st["reason_code"] == "PHASE3_FAILED", f"Stage {stage_name} reason_code should be PHASE3_FAILED"

    assert p4_sec["execution"]["result"] is None


def test_valid_rl_abstain_has_status_success():
    """A valid RL advisory object returning ABSTAIN must report status=SUCCESS."""
    valid_abstain_obj = {
        "status": "SUCCESS",
        "operating_mode": "SHADOW",
        "policy_version": "v1.0",
        "model_version": "v1.0",
        "recommendation": "ABSTAIN",
        "allowed_actions": ["ACCEPT_PROPOSAL", "ABSTAIN"],
        "action_scores": {"ABSTAIN": 1.0},
        "uncertainty": 0.0,
        "sample_size": 10,
        "cold_start": False,
        "influence_allowed": False,
        "reason_codes": ["LOW_CONFIDENCE"],
        "feature_hash": "feat_hash_123",
        "latency_ms": 5.0
    }

    rl_sec = _build_rl_advisory_section(valid_abstain_obj)

    assert rl_sec["status"] == "SUCCESS"
    assert rl_sec["recommendation"] == "ABSTAIN"
    assert rl_sec["influence_allowed"] is False


def test_rl_advisor_init_exception_produces_unavailable_advisory_and_complete_report(tmp_path):
    """Constructor failure in RLAdvisor must produce status=UNAVAILABLE advisory and allow report generation to complete."""
    with patch("rl_engine.advisor.RLAdvisor.__init__", side_effect=RuntimeError("GPU VRAM connection error")):
        # run_single_problem should handle RLAdvisor init failure gracefully
        result = run_single_problem("problems/case_01.json", reports_base_dir=str(tmp_path))
        assert os.path.exists(result["json_report"])
        assert os.path.exists(result["md_report"])

        with open(result["json_report"], "r", encoding="utf-8") as f:
            report_data = json.load(f)

        rl_sec = report_data["rl_advisory"]
        assert rl_sec["status"] == "UNAVAILABLE"
        assert rl_sec["recommendation"] == "ABSTAIN"
        assert rl_sec["feature_hash"] == "UNAVAILABLE"
        assert rl_sec["influence_allowed"] is False
        assert "RL_ADVISOR_EXCEPTION" in rl_sec["reason_codes"]


def test_episode_stored_value_matches_episode_store(mock_debate_manager):
    """learning.stored must equal the boolean returned by EpisodeStore.save_episode()."""
    p4_sec = {
        "status": "SIMULATION_VERIFIED",
        "execution": {"attempted": True}
    }

    sec_true = _build_learning_section(None, is_simulated=True, p4_sec=p4_sec, episode_stored=True)
    assert sec_true["stored"] is True

    sec_false = _build_learning_section(None, is_simulated=True, p4_sec=p4_sec, episode_stored=False)
    assert sec_false["stored"] is False


def test_simulation_may_be_stored_but_remains_learning_ineligible():
    """Simulation episodes may have stored=true but must remain eligible=false, reward=null, sample_weight=0.0."""
    p4_sec = {"status": "SIMULATION_VERIFIED", "execution": {"attempted": True}}

    learning_sec = _build_learning_section(None, is_simulated=True, p4_sec=p4_sec, episode_stored=True)

    assert learning_sec["stored"] is True
    assert learning_sec["eligible"] is False
    assert learning_sec["reward"] is None
    assert learning_sec["sample_weight"] == 0.0


def test_json_restoration_failure_raises_report_atomicity_error(tmp_path):
    """If JSON restoration fails after replacement error, ReportAtomicityError is raised and backup files retained."""
    context = {
        "schema_version": "phase34-report-v1",
        "report_type": "PHASE34_PROBLEM_SUMMARY",
        "run": {
            "verification_run_id": "verify_atomic_fail",
            "problem_run_id": "run_atomic_fail",
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

    # 2. Inject failure on replacement AND on restoration
    original_replace = os.replace

    def double_failing_replace(src, dst):
        if ".phase34_report.md.tmp." in src:
            raise OSError("Replacement error for MD")
        if ".phase34_report.json.bak." in src:
            raise OSError("Restoration error for JSON bak")
        return original_replace(src, dst)

    with patch("os.replace", side_effect=double_failing_replace):
        with pytest.raises(ReportAtomicityError, match="Report pair replacement failed"):
            generate_phase34_report(context, reports_base_dir=str(tmp_path))

    dest_dir = Path(j_orig).parent
    # Assert temp files were removed
    tmp_files = [p.name for p in dest_dir.iterdir() if ".tmp." in p.name]
    assert len(tmp_files) == 0, f"Temporary files should be removed: {tmp_files}"

    # Assert recoverable .bak files were retained
    bak_files = [p.name for p in dest_dir.iterdir() if ".bak." in p.name]
    assert len(bak_files) > 0, f"Bak files should be retained on restoration failure"


# ─────────────────────────────────────────────────────────────────────────────
# Individual Atomic Failure-Injection Tests
# ─────────────────────────────────────────────────────────────────────────────


def _make_minimal_context(tmp_path: Path) -> dict:
    """Return a minimal valid report context to drive generate_phase34_report."""
    return {
        "schema_version": "phase34-report-v1",
        "report_type": "PHASE34_PROBLEM_SUMMARY",
        "run": {
            "verification_run_id": f"verify_{uuid.uuid4().hex[:8]}",
            "problem_run_id": f"run_{uuid.uuid4().hex[:8]}",
            "commit_sha": "a" * 40,
            "started_at": "2026-09-03T12:00:00Z",
            "completed_at": "2026-09-03T12:00:05Z",
            "duration_ms": 5000,
            "execution_mode": "SIMULATION",
            "mock_llm": True,
            "rl_operating_mode": "SHADOW",
            "laptop1_transport": "disabled"
        },
        "problem": {
            "case_id": "case_atomic",
            "source_file": "problems/case_atomic.json",
            "input_hash": "b" * 64,
            "raw_input": {"incident_id": "case_atomic"},
            "normalized_incident": {"type": "disk_pressure"},
            "severity": "HIGH",
            "target": {"kind": "container", "canonical_name": "test-svc"},
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
            "orchestrator_decision": "TIER_1_AUTONOMOUS_EXECUTION",
            "reason_codes": ["DIAGNOSED"]
        },
        "phase_3_to_4_handoff": {
            "status": "SUCCESS", "schema_valid": True, "validation_errors": [], "payload_hash": "c" * 64,
            "exact_envelope": {"schema_version": "2.0"}, "capability_mapped": True, "mvp_supported": True, "target_resolved": True
        },
        "rl_advisory": {
            "status": "SUCCESS", "operating_mode": "SHADOW", "policy_version": "v1.0", "model_version": "v1.0",
            "recommendation": "ACCEPT_PROPOSAL", "allowed_actions": ["ACCEPT_PROPOSAL"], "action_scores": {"ACCEPT_PROPOSAL": 0.9},
            "uncertainty": 0.1, "sample_size": 5, "cold_start": False, "influence_allowed": False, "reason_codes": [],
            "feature_hash": "d" * 64, "latency_ms": 10
        },
        "phase_4": {
            "status": "SIMULATION_VERIFIED", "started_at": "2026-09-03T12:00:03Z", "completed_at": "2026-09-03T12:00:05Z",
            "duration_ms": 2000, "exact_input": {"schema_version": "2.0"}, "target": {"kind": "container", "canonical_name": "test-svc"},
            "attestation": {"status": "PASSED", "attempted": True, "reason_code": "PASSED", "reason": "OK", "data": {}, "duration_ms": 5},
            "before_observations": {"status": "COMPLETED", "attempted": True, "reason_code": "OBSERVED", "reason": "OK", "data": {}, "duration_ms": 5},
            "fault_setup": {"status": "COMPLETED", "attempted": True, "reason_code": "FAULTED", "reason": "OK", "data": {}, "duration_ms": 5},
            "execution": {"status": "SUCCESS", "attempted": True, "capability": "container.restart", "mode": "MUTATE_REVERSIBLE", "parameters": {}, "result": {"success": True}, "duration_ms": 50},
            "after_observations": {"status": "COMPLETED", "attempted": True, "reason_code": "OBSERVED", "reason": "OK", "data": {}, "duration_ms": 5},
            "verification": {"status": "PASSED", "attempted": True, "reason_code": "VERIFIED_RECOVERED", "reason": "OK", "data": {}, "duration_ms": 5},
            "rollback": {"status": "NOT_RUN", "attempted": False, "reason_code": "NOT_RUN", "reason": "Not run", "data": {}, "duration_ms": 0},
            "cleanup": {"status": "COMPLETED", "attempted": True, "reason_code": "CLEANED_UP", "reason": "OK", "data": {}, "duration_ms": 5},
            "state_history": ["REPORTED"], "reason_codes": ["VERIFIED_RECOVERED"]
        },
        "learning": {
            "status": "NOT_ELIGIBLE", "episode_id": "ep_123", "eligible": False, "eligibility_reason": "SIMULATION_MODE",
            "behavior_action": "ACCEPT_PROPOSAL", "reward": None, "sample_weight": 0.0, "feature_hash": "d" * 64, "stored": False
        },
        "final_summary": {
            "outcome": "SIMULATION_VERIFIED", "problem_resolved_in_sandbox": False, "execution_performed": True,
            "human_intervention_required": False, "recommended_next_action": "RUN_REAL_SHADOW_VALIDATION",
            "what_happened": "Verified", "why_it_happened": "Success", "safety_result": "PASS",
            "confidence_result": "HIGH", "limitations": ["Simulation mode"]
        },
        "integrity": {
            "report_schema_valid": True, "input_hash": "b" * 64, "payload_hash": "c" * 64,
            "event_log_hash": EMPTY_EVENT_LOG_HASH, "report_hash": "0" * 64, "errors": []
        }
    }


import uuid


def test_atomic_tmp_json_write_failure_cleans_up(tmp_path):
    """If the temporary JSON write fails, no .tmp files are left behind."""
    ctx = _make_minimal_context(tmp_path)
    orig_open = open

    def failing_open(path, *args, **kwargs):
        if isinstance(path, str) and ".phase34_report.json.tmp." in path:
            raise OSError("Simulated JSON tmp write failure")
        return orig_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=failing_open):
        with pytest.raises(OSError, match="Simulated JSON tmp write failure"):
            generate_phase34_report(ctx, reports_base_dir=str(tmp_path))

    for f in tmp_path.rglob("*.tmp.*"):
        assert False, f"tmp file left behind: {f}"


def test_atomic_tmp_md_write_failure_cleans_up(tmp_path):
    """If the temporary Markdown write fails, no .tmp files are left behind."""
    ctx = _make_minimal_context(tmp_path)
    orig_open = open

    def failing_open(path, *args, **kwargs):
        if isinstance(path, str) and ".phase34_report.md.tmp." in path:
            raise OSError("Simulated MD tmp write failure")
        return orig_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=failing_open):
        with pytest.raises(OSError, match="Simulated MD tmp write failure"):
            generate_phase34_report(ctx, reports_base_dir=str(tmp_path))

    for f in tmp_path.rglob("*.tmp.*"):
        assert False, f"tmp file left behind: {f}"


def test_atomic_json_backup_failure_cleans_tmp_files(tmp_path):
    """If JSON backup (dest→bak) fails, .tmp files are cleaned up."""
    ctx = _make_minimal_context(tmp_path)
    # Create an original file pair first
    j, m = generate_phase34_report(ctx, reports_base_dir=str(tmp_path))
    orig_replace = os.replace

    def failing_replace(src, dst):
        if ".phase34_report.json.bak." in dst:
            raise OSError("Simulated JSON backup failure")
        return orig_replace(src, dst)

    with patch("os.replace", side_effect=failing_replace):
        with pytest.raises(OSError):
            generate_phase34_report(ctx, reports_base_dir=str(tmp_path))

    dest_dir = Path(j).parent
    tmp_files = [p.name for p in dest_dir.iterdir() if ".tmp." in p.name]
    assert len(tmp_files) == 0, f"tmp files should be cleaned on backup failure: {tmp_files}"


def test_atomic_md_backup_failure_cleans_tmp_files(tmp_path):
    """If MD backup (dest→bak) fails, .tmp files are cleaned up."""
    ctx = _make_minimal_context(tmp_path)
    j, m = generate_phase34_report(ctx, reports_base_dir=str(tmp_path))
    orig_replace = os.replace

    def failing_replace(src, dst):
        if ".phase34_report.md.bak." in dst:
            raise OSError("Simulated MD backup failure")
        return orig_replace(src, dst)

    with patch("os.replace", side_effect=failing_replace):
        with pytest.raises(OSError):
            generate_phase34_report(ctx, reports_base_dir=str(tmp_path))

    dest_dir = Path(j).parent
    tmp_files = [p.name for p in dest_dir.iterdir() if ".tmp." in p.name]
    assert len(tmp_files) == 0, f"tmp files should be cleaned on MD backup failure: {tmp_files}"


def test_atomic_json_replacement_failure_removes_tmp_and_restores(tmp_path):
    """If the JSON tmp→dest replacement fails, tmp files are removed and backup restored."""
    ctx = _make_minimal_context(tmp_path)
    j, m = generate_phase34_report(ctx, reports_base_dir=str(tmp_path))
    orig_replace = os.replace

    def failing_replace(src, dst):
        if ".phase34_report.json.tmp." in src and not ".bak." in src:
            raise OSError("Simulated JSON replacement failure")
        return orig_replace(src, dst)

    with patch("os.replace", side_effect=failing_replace):
        with pytest.raises(OSError):
            generate_phase34_report(ctx, reports_base_dir=str(tmp_path))

    dest_dir = Path(j).parent
    tmp_files = [p.name for p in dest_dir.iterdir() if ".tmp." in p.name]
    assert len(tmp_files) == 0, f"tmp files should be removed after JSON replacement failure: {tmp_files}"
    assert os.path.exists(j), "Original JSON should be restored after JSON replacement failure"


def test_atomic_md_replacement_failure_removes_tmp_and_restores(tmp_path):
    """If the MD tmp→dest replacement fails, tmp files are removed and backup restored."""
    ctx = _make_minimal_context(tmp_path)
    j, m = generate_phase34_report(ctx, reports_base_dir=str(tmp_path))
    orig_replace = os.replace

    def failing_replace(src, dst):
        if ".phase34_report.md.tmp." in src and ".bak." not in src:
            raise OSError("Simulated MD replacement failure")
        return orig_replace(src, dst)

    with patch("os.replace", side_effect=failing_replace):
        with pytest.raises(OSError):
            generate_phase34_report(ctx, reports_base_dir=str(tmp_path))

    dest_dir = Path(j).parent
    tmp_files = [p.name for p in dest_dir.iterdir() if ".tmp." in p.name]
    assert len(tmp_files) == 0, f"tmp files should be removed after MD replacement failure: {tmp_files}"
    assert os.path.exists(m), "Original MD should be restored after MD replacement failure"


def test_atomic_json_restoration_failure_raises_atomicity_error(tmp_path):
    """If JSON replacement fails and JSON bak restoration also fails, ReportAtomicityError is raised."""
    ctx = _make_minimal_context(tmp_path)
    j, m = generate_phase34_report(ctx, reports_base_dir=str(tmp_path))
    orig_replace = os.replace

    def double_failing_replace(src, dst):
        if ".phase34_report.json.tmp." in src and ".bak." not in src:
            raise OSError("JSON replacement failure")
        if ".phase34_report.json.bak." in src:
            raise OSError("JSON restoration failure")
        return orig_replace(src, dst)

    with patch("os.replace", side_effect=double_failing_replace):
        with pytest.raises(ReportAtomicityError):
            generate_phase34_report(ctx, reports_base_dir=str(tmp_path))

    dest_dir = Path(j).parent
    tmp_files = [p.name for p in dest_dir.iterdir() if ".tmp." in p.name]
    assert len(tmp_files) == 0, f"tmp files should be removed even on atomicity error: {tmp_files}"


def test_atomic_md_restoration_failure_raises_atomicity_error(tmp_path):
    """If MD replacement fails and MD bak restoration also fails, ReportAtomicityError is raised."""
    ctx = _make_minimal_context(tmp_path)
    j, m = generate_phase34_report(ctx, reports_base_dir=str(tmp_path))
    orig_replace = os.replace

    def double_failing_replace(src, dst):
        if ".phase34_report.md.tmp." in src and ".bak." not in src:
            raise OSError("MD replacement failure")
        if ".phase34_report.md.bak." in src:
            raise OSError("MD restoration failure")
        return orig_replace(src, dst)

    with patch("os.replace", side_effect=double_failing_replace):
        with pytest.raises(ReportAtomicityError):
            generate_phase34_report(ctx, reports_base_dir=str(tmp_path))

    dest_dir = Path(j).parent
    tmp_files = [p.name for p in dest_dir.iterdir() if ".tmp." in p.name]
    assert len(tmp_files) == 0, f"tmp files should be removed even on atomicity error: {tmp_files}"


def test_atomic_successful_write_cleans_all_temp_and_bak_files(tmp_path):
    """A fully successful write leaves no .tmp or .bak files in the destination directory."""
    ctx = _make_minimal_context(tmp_path)
    j, m = generate_phase34_report(ctx, reports_base_dir=str(tmp_path))

    dest_dir = Path(j).parent
    tmp_files = [p.name for p in dest_dir.iterdir() if ".tmp." in p.name]
    bak_files = [p.name for p in dest_dir.iterdir() if ".bak." in p.name]
    assert len(tmp_files) == 0, f"No tmp files should remain after success: {tmp_files}"
    assert len(bak_files) == 0, f"No bak files should remain after success: {bak_files}"
    assert os.path.exists(j)
    assert os.path.exists(m)
