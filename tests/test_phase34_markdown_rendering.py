import os
import json
import copy
import pytest
from shadow_sandbox.reports.report_generator import generate_phase34_report, EMPTY_EVENT_LOG_HASH


@pytest.fixture
def valid_report_context():
    return {
        "schema_version": "phase34-report-v1",
        "report_type": "PHASE34_PROBLEM_SUMMARY",
        "run": {
            "verification_run_id": "verify_mdtest123",
            "problem_run_id": "run_mdtest123",
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
                "score": 0.88,
                "threshold": 0.80,
                "uncertainty": 0.12,
                "calibration_status": "CALIBRATED",
                "evidence_count": 2,
                "component_agreement": 1.0,
                "evidence_grounding": 0.88,
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
            "recommended_next_action": "RUN_REAL_SHADOW_VALIDATION",
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


def test_markdown_and_json_contain_same_ids(tmp_path, valid_report_context):
    j_path, m_path = generate_phase34_report(valid_report_context, reports_base_dir=str(tmp_path))

    with open(j_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    with open(m_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    assert data["run"]["verification_run_id"] in md_text
    assert data["run"]["problem_run_id"] in md_text
    assert data["problem"]["case_id"] in md_text
    assert data["integrity"]["report_hash"] in md_text


def test_markdown_outcome_matches_json_final_summary_outcome(tmp_path, valid_report_context):
    j_path, m_path = generate_phase34_report(valid_report_context, reports_base_dir=str(tmp_path))

    with open(j_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    with open(m_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    outcome = data["final_summary"]["outcome"]
    assert f"- **Outcome**: `{outcome}`" in md_text


def test_markdown_confidence_matches_json_phase3_confidence_score(tmp_path, valid_report_context):
    j_path, m_path = generate_phase34_report(valid_report_context, reports_base_dir=str(tmp_path))

    with open(j_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    with open(m_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    score = data["phase_3"]["confidence"]["score"]
    assert f"- **Score**: `{score}`" in md_text


def test_final_summary_appears_before_phase3_details(tmp_path, valid_report_context):
    _, m_path = generate_phase34_report(valid_report_context, reports_base_dir=str(tmp_path))

    with open(m_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    pos_title = md_text.find("# Phase 3–4 Problem Summary")
    pos_summary = md_text.find("## Final Summary")
    pos_run = md_text.find("## Run Identity")
    pos_p3 = md_text.find("## Phase 3 Debate")

    assert pos_title != -1
    assert pos_summary != -1
    assert pos_run != -1
    assert pos_p3 != -1

    assert pos_title < pos_summary < pos_run < pos_p3


def test_blocked_stages_render_not_run_and_reason(tmp_path, valid_report_context):
    blocked_ctx = copy.deepcopy(valid_report_context)
    blocked_ctx["phase_4"]["attestation"] = {
        "status": "NOT_RUN",
        "attempted": False,
        "reason_code": "BLOCKED_SAFETY",
        "reason": "Stage blocked by safety gate",
        "data": {},
        "duration_ms": 0
    }
    blocked_ctx["phase_4"]["execution"] = {
        "status": "NOT_RUN",
        "attempted": False,
        "capability": "container.restart",
        "mode": "MUTATE_HIGH_RISK",
        "parameters": {},
        "result": {"reason": "High risk action requires human review"},
        "duration_ms": 0
    }

    _, m_path = generate_phase34_report(blocked_ctx, reports_base_dir=str(tmp_path))

    with open(m_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    assert "`NOT_RUN`" in md_text
    assert "Stage blocked by safety gate" in md_text


def test_simulation_does_not_claim_real_sandbox_resolution(tmp_path, valid_report_context):
    sim_ctx = copy.deepcopy(valid_report_context)
    sim_ctx["run"]["execution_mode"] = "SIMULATION"
    sim_ctx["final_summary"]["problem_resolved_in_sandbox"] = False

    j_path, m_path = generate_phase34_report(sim_ctx, reports_base_dir=str(tmp_path))

    with open(j_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    with open(m_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    assert data["final_summary"]["problem_resolved_in_sandbox"] is False
    assert "- **Problem Resolved in Sandbox**: `False`" in md_text
