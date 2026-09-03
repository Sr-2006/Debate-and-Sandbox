import os
import json
import copy
import pytest
from pathlib import Path
from unittest.mock import patch

from shadow_sandbox.reports.report_generator import (
    generate_phase34_report,
    compute_report_hash,
    ReportContractError,
    load_report_schema,
    get_format_checker,
    EMPTY_EVENT_LOG_HASH,
)
from jsonschema import Draft7Validator
from run_mvp_pipeline import run_single_problem


@pytest.fixture
def valid_report_context():
    return {
        "schema_version": "phase34-report-v1",
        "report_type": "PHASE34_PROBLEM_SUMMARY",
        "run": {
            "verification_run_id": "verify_test12345",
            "problem_run_id": "run_test12345",
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
                "optimist": {
                    "name": "optimist",
                    "status": "SUCCESS",
                    "prompt": "prompt_opt",
                    "raw_response": "raw_opt",
                    "parsed_response": {"intent": "container.restart"},
                    "valid": True,
                    "latency_ms": 500,
                    "error": None
                },
                "critic": {
                    "name": "critic",
                    "status": "SUCCESS",
                    "prompt": "prompt_crit",
                    "raw_response": "raw_crit",
                    "parsed_response": {"safety": "PASS"},
                    "valid": True,
                    "latency_ms": 400,
                    "error": None
                },
                "fact_checker": {
                    "name": "fact_checker",
                    "status": "SUCCESS",
                    "prompt": "prompt_fc",
                    "raw_response": "raw_fc",
                    "parsed_response": {"evidence": True},
                    "valid": True,
                    "latency_ms": 450,
                    "error": None
                }
            },
            "agreement": 1.0,
            "confidence": {
                "score": 0.90,
                "threshold": 0.80,
                "uncertainty": 0.10,
                "calibration_status": "CALIBRATED",
                "evidence_count": 2,
                "component_agreement": 1.0,
                "evidence_grounding": 0.90,
                "veto_applied": False,
                "veto_cap": None,
                "reason_codes": ["DIAGNOSED"]
            },
            "safety": {"status": "PASS"},
            "selected_intent": {"intent_type": "container.restart"},
            "orchestrator_decision": "APPROVE",
            "reason_codes": ["DIAGNOSED"]
        },
        "phase_3_to_4_handoff": {
            "status": "SUCCESS",
            "schema_valid": True,
            "validation_errors": [],
            "payload_hash": "b" * 64,
            "exact_envelope": {"schema_version": "2.0"},
            "capability_mapped": True,
            "mvp_supported": True,
            "target_resolved": True
        },
        "rl_advisory": {
            "status": "SUCCESS",
            "operating_mode": "SHADOW",
            "policy_version": "v1.0",
            "model_version": "v1.0",
            "recommendation": "ACCEPT_PROPOSAL",
            "allowed_actions": ["ACCEPT_PROPOSAL"],
            "action_scores": {"ACCEPT_PROPOSAL": 0.9},
            "uncertainty": 0.1,
            "sample_size": 5,
            "cold_start": False,
            "influence_allowed": False,
            "reason_codes": [],
            "feature_hash": "c" * 64,
            "latency_ms": 10
        },
        "phase_4": {
            "status": "SIMULATION_VERIFIED",
            "started_at": "2026-09-03T12:00:03Z",
            "completed_at": "2026-09-03T12:00:05Z",
            "duration_ms": 2000,
            "exact_input": {"schema_version": "2.0"},
            "target": {"kind": "container", "canonical_name": "postgres-db"},
            "attestation": {
                "status": "PASSED",
                "attempted": True,
                "reason_code": "DIAGNOSED",
                "reason": "Simulated attestation verified",
                "data": {},
                "duration_ms": 5
            },
            "before_observations": {
                "status": "COMPLETED",
                "attempted": True,
                "reason_code": "DIAGNOSED",
                "reason": "Observed pre-state",
                "data": {},
                "duration_ms": 10
            },
            "fault_setup": {
                "status": "COMPLETED",
                "attempted": True,
                "reason_code": "DIAGNOSED",
                "reason": "Fault injected",
                "data": {},
                "duration_ms": 10
            },
            "execution": {
                "status": "SUCCESS",
                "attempted": True,
                "capability": "container.restart",
                "mode": "MUTATE_REVERSIBLE",
                "parameters": {},
                "result": {"success": True},
                "duration_ms": 50
            },
            "after_observations": {
                "status": "COMPLETED",
                "attempted": True,
                "reason_code": "DIAGNOSED",
                "reason": "Observed post-state",
                "data": {},
                "duration_ms": 10
            },
            "verification": {
                "status": "PASSED",
                "attempted": True,
                "reason_code": "VERIFIED_RECOVERED",
                "reason": "Service restored",
                "data": {},
                "duration_ms": 20
            },
            "rollback": {
                "status": "NOT_RUN",
                "attempted": False,
                "reason_code": "NOT_RUN",
                "reason": "No rollback needed",
                "data": {},
                "duration_ms": 0
            },
            "cleanup": {
                "status": "COMPLETED",
                "attempted": True,
                "reason_code": "DIAGNOSED",
                "reason": "Cleanup finished",
                "data": {},
                "duration_ms": 5
            },
            "state_history": ["RECEIVED", "VALIDATED", "OBSERVED_BEFORE", "EXECUTED_OR_BLOCKED", "OBSERVED_AFTER", "VERIFIED_OR_ROLLED_BACK", "REPORTED"],
            "reason_codes": ["VERIFIED_RECOVERED"]
        },
        "learning": {
            "status": "NOT_ELIGIBLE",
            "episode_id": "ep_test123",
            "eligible": False,
            "eligibility_reason": "SIMULATION_MODE",
            "behavior_action": "ACCEPT_PROPOSAL",
            "reward": None,
            "sample_weight": 0.0,
            "feature_hash": "c" * 64,
            "stored": True
        },
        "final_summary": {
            "outcome": "SIMULATION_VERIFIED",
            "problem_resolved_in_sandbox": False,
            "execution_performed": True,
            "human_intervention_required": False,
            "recommended_next_action": "NO_ACTION_REQUIRED",
            "what_happened": "Remediation verified in simulation mode.",
            "why_it_happened": "Container restart succeeded.",
            "safety_result": "PASS",
            "confidence_result": "HIGH",
            "limitations": ["Simulation execution mode active"]
        },
        "integrity": {
            "report_schema_valid": True,
            "input_hash": "a" * 64,
            "payload_hash": "b" * 64,
            "event_log_hash": EMPTY_EVENT_LOG_HASH,
            "report_hash": "0" * 64,
            "errors": ["PHASE34_EVENT_LOG_NOT_EMITTED_PHASE3"]
        }
    }


def test_valid_canonical_context_creates_json_and_markdown(tmp_path, valid_report_context):
    j_path, m_path = generate_phase34_report(valid_report_context, reports_base_dir=str(tmp_path))

    assert os.path.exists(j_path)
    assert os.path.exists(m_path)
    assert j_path.endswith(os.path.join("verify_test12345", "cases", "case_01", "phase34_report.json"))
    assert m_path.endswith(os.path.join("verify_test12345", "cases", "case_01", "phase34_report.md"))

    with open(j_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["run"]["verification_run_id"] == "verify_test12345"
        assert data["problem"]["case_id"] == "case_01"


def test_invalid_context_creates_no_files(tmp_path, valid_report_context):
    invalid_ctx = copy.deepcopy(valid_report_context)
    del invalid_ctx["final_summary"]

    with pytest.raises(ReportContractError):
        generate_phase34_report(invalid_ctx, reports_base_dir=str(tmp_path))

    target_dir = tmp_path / "verify_test12345"
    assert not target_dir.exists()


def test_invalid_timestamp_is_rejected(tmp_path, valid_report_context):
    invalid_ctx = copy.deepcopy(valid_report_context)
    invalid_ctx["run"]["started_at"] = "invalid-date-format"

    with pytest.raises(ReportContractError):
        generate_phase34_report(invalid_ctx, reports_base_dir=str(tmp_path))


def test_missing_required_section_is_rejected(tmp_path, valid_report_context):
    invalid_ctx = copy.deepcopy(valid_report_context)
    del invalid_ctx["integrity"]

    with pytest.raises(ReportContractError):
        generate_phase34_report(invalid_ctx, reports_base_dir=str(tmp_path))


def test_extra_nested_property_is_rejected(tmp_path, valid_report_context):
    invalid_ctx = copy.deepcopy(valid_report_context)
    invalid_ctx["run"]["extra_field"] = "not_allowed"

    with pytest.raises(ReportContractError):
        generate_phase34_report(invalid_ctx, reports_base_dir=str(tmp_path))


def test_report_hash_recomputes_correctly(tmp_path, valid_report_context):
    j_path, _ = generate_phase34_report(valid_report_context, reports_base_dir=str(tmp_path))

    with open(j_path, "r", encoding="utf-8") as f:
        stored_data = json.load(f)

    stored_hash = stored_data["integrity"]["report_hash"]
    computed_hash = compute_report_hash(stored_data)

    assert stored_hash == computed_hash
    assert len(stored_hash) == 64


def test_identical_nonvolatile_input_produces_identical_report_hash(valid_report_context):
    ctx1 = copy.deepcopy(valid_report_context)
    ctx2 = copy.deepcopy(valid_report_context)

    h1 = compute_report_hash(ctx1)
    h2 = compute_report_hash(ctx2)

    assert h1 == h2


def test_failure_during_markdown_write_leaves_no_incomplete_pair(tmp_path, valid_report_context):
    with patch("shadow_sandbox.reports.report_generator._render_phase34_markdown", side_effect=RuntimeError("MD Error")):
        with pytest.raises(RuntimeError):
            generate_phase34_report(valid_report_context, reports_base_dir=str(tmp_path))

    dest_dir = tmp_path / "verify_test12345" / "cases" / "case_01"
    json_path = dest_dir / "phase34_report.json"
    md_path = dest_dir / "phase34_report.md"

    assert not json_path.exists()
    assert not md_path.exists()


def test_legacy_producer_cannot_write_into_canonical_directory(tmp_path, valid_report_context):
    from shadow_sandbox.reports.legacy_report_generator import generate_mvp_report
    j_path, m_path = generate_mvp_report(valid_report_context, reports_base_dir=str(tmp_path))

    assert "cases" not in Path(j_path).parts
    assert Path(j_path).name != "phase34_report.json"


def test_coordinator_smoke_test(tmp_path):
    """Section 14: Coordinator Smoke Test running problems/case_01.json."""
    os.environ["DEBATE_MOCK_LLM"] = "1"
    os.environ["RL_OPERATING_MODE"] = "SHADOW"
    os.environ["RL_LAPTOP1_TRANSPORT"] = "disabled"

    problem_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "problems", "case_01.json")
    assert os.path.exists(problem_file)

    res = run_single_problem(problem_file, reports_base_dir=str(tmp_path))

    verif_id = res["verification_run_id"]
    json_path = tmp_path / verif_id / "cases" / "case_01" / "phase34_report.json"
    md_path = tmp_path / verif_id / "cases" / "case_01" / "phase34_report.md"

    assert json_path.exists()
    assert md_path.exists()

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Validate JSON against real Draft 7 schema
    schema = load_report_schema()
    validator = Draft7Validator(schema, format_checker=get_format_checker())
    errors = list(validator.iter_errors(data))
    assert len(errors) == 0, f"Schema validation errors: {[e.message for e in errors]}"

    assert data["run"]["execution_mode"] == "SIMULATION"
    assert data["learning"]["eligible"] is False
    assert data["learning"]["reward"] is None
    assert data["learning"]["sample_weight"] == 0.0
    assert data["final_summary"]["problem_resolved_in_sandbox"] is False
    assert "PHASE34_EVENT_LOG_NOT_EMITTED_PHASE3" in data["integrity"]["errors"]
