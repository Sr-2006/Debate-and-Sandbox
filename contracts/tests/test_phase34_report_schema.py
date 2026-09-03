import json
from pathlib import Path
import pytest
from jsonschema import validate, ValidationError

CONTRACTS_DIR = Path(__file__).parent.parent

def load_schema(schema_name: str):
    schema_path = CONTRACTS_DIR / schema_name
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)

@pytest.fixture
def report_schema():
    return load_schema("phase34_report_v1.schema.json")

@pytest.fixture
def event_schema():
    return load_schema("phase34_event_v1.schema.json")

@pytest.fixture
def valid_report_dict():
    return {
        "schema_version": "phase34-report-v1",
        "report_type": "PHASE34_PROBLEM_SUMMARY",
        "run": {
            "verification_run_id": "verif_run_001",
            "problem_run_id": "prob_run_001",
            "commit_sha": "abc123def456",
            "started_at": "2026-09-03T12:00:00Z",
            "completed_at": "2026-09-03T12:00:05Z",
            "duration_ms": 5000,
            "execution_mode": "SIMULATION",
            "mock_llm": True,
            "rl_operating_mode": "SHADOW",
            "laptop1_transport": None
        },
        "problem": {
            "case_id": "case_01",
            "source_file": "problems/case_01.json",
            "input_hash": "hash_input_123",
            "raw_input": {"problem_name": "disk_pressure"},
            "normalized_incident": {"type": "disk_full"},
            "severity": "HIGH",
            "target": {"kind": "container", "canonical_name": "postgres-db"},
            "expected_behavior": "Clear old WAL files"
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
                    "prompt": "Analyze root cause...",
                    "raw_response": "Proposed fix...",
                    "parsed_response": {"intent": "truncate_log"},
                    "valid": True,
                    "latency_ms": 500,
                    "error": None
                },
                "critic": {
                    "name": "critic",
                    "status": "SUCCESS",
                    "prompt": "Evaluate safety...",
                    "raw_response": "Approved...",
                    "parsed_response": {"safety": "PASS"},
                    "valid": True,
                    "latency_ms": 400,
                    "error": None
                },
                "fact_checker": {
                    "name": "fact_checker",
                    "status": "SUCCESS",
                    "prompt": "Check facts...",
                    "raw_response": "Verified...",
                    "parsed_response": {"evidence": True},
                    "valid": True,
                    "latency_ms": 450,
                    "error": None
                }
            },
            "agreement": 1.0,
            "confidence": {
                "score": 0.95,
                "threshold": 0.8,
                "uncertainty": 0.05,
                "calibration_status": "CALIBRATED",
                "evidence_count": 3,
                "component_agreement": 1.0,
                "evidence_grounding": 0.9,
                "veto_applied": False,
                "veto_cap": None,
                "reason_codes": []
            },
            "safety": {"status": "PASS"},
            "selected_intent": {"intent_type": "workload.restart"},
            "orchestrator_decision": "APPROVE",
            "reason_codes": ["DIAGNOSED"]
        },
        "phase_3_to_4_handoff": {
            "status": "SUCCESS",
            "schema_valid": True,
            "validation_errors": [],
            "payload_hash": "payload_hash_123",
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
            "sample_size": 10,
            "cold_start": False,
            "influence_allowed": False,
            "reason_codes": [],
            "feature_hash": "feat_hash_123",
            "latency_ms": 15
        },
        "phase_4": {
            "status": "COMPLETED",
            "started_at": "2026-09-03T12:00:03Z",
            "completed_at": "2026-09-03T12:00:05Z",
            "duration_ms": 2000,
            "exact_input": {"target": "postgres-db"},
            "target": {"kind": "container", "canonical_name": "postgres-db"},
            "attestation": {
                "status": "PASSED",
                "attempted": True,
                "attested": True,
                "target": "postgres-db",
                "environment": "SHADOW_SANDBOX",
                "reason_code": "ATTESTED",
                "reason": "Target verified"
            },
            "before_observations": [],
            "fault_setup": {"status": "SUCCESS"},
            "execution": {
                "status": "SUCCESS",
                "attempted": True,
                "capability": "workload.restart",
                "mode": "SIMULATION",
                "parameters": {},
                "result": {"output": "ok"},
                "duration_ms": 100
            },
            "after_observations": [],
            "verification": {"status": "PASSED"},
            "rollback": None,
            "cleanup": {"status": "COMPLETED"},
            "state_history": [],
            "reason_codes": ["VERIFIED_RECOVERED"]
        },
        "learning": {
            "status": "NOT_ELIGIBLE",
            "episode_id": "ep_001",
            "eligible": False,
            "eligibility_reason": "SIMULATION_MODE",
            "behavior_action": "ACCEPT_PROPOSAL",
            "reward": None,
            "sample_weight": 0.0,
            "feature_hash": "feat_hash_123",
            "stored": True
        },
        "final_summary": {
            "outcome": "SUCCESS",
            "problem_resolved_in_sandbox": True,
            "execution_performed": True,
            "human_intervention_required": False,
            "recommended_next_action": "NO_ACTION_REQUIRED",
            "what_happened": "Remediation executed in shadow sandbox.",
            "why_it_happened": "Disk pressure resolved.",
            "safety_result": "PASS",
            "confidence_result": "HIGH",
            "limitations": ["Shadow sandbox only"]
        },
        "integrity": {
            "report_schema_valid": True,
            "input_hash": "hash_input_123",
            "payload_hash": "payload_hash_123",
            "event_log_hash": "event_log_hash_123",
            "report_hash": "report_hash_123",
            "errors": []
        }
    }

def test_valid_report_schema(report_schema, valid_report_dict):
    validate(instance=valid_report_dict, schema=report_schema)

def test_invalid_schema_version(report_schema, valid_report_dict):
    valid_report_dict["schema_version"] = "v1.0"
    with pytest.raises(ValidationError):
        validate(instance=valid_report_dict, schema=report_schema)

def test_missing_required_section(report_schema, valid_report_dict):
    del valid_report_dict["integrity"]
    with pytest.raises(ValidationError):
        validate(instance=valid_report_dict, schema=report_schema)

def test_unavailable_work_structure(report_schema, valid_report_dict):
    # Unavailable work pattern represented cleanly with NOT_RUN
    valid_report_dict["phase_4"]["status"] = "NOT_RUN"
    valid_report_dict["phase_4"]["reason_codes"] = ["BLOCKED_SAFETY"]
    valid_report_dict["phase_4"]["attestation"] = {
        "status": "NOT_RUN",
        "attempted": False,
        "attested": False,
        "target": "postgres-db",
        "environment": "SHADOW_SANDBOX",
        "reason_code": "BLOCKED_SAFETY",
        "reason": "High-risk mutation requires human approval"
    }
    validate(instance=valid_report_dict, schema=report_schema)

def test_valid_event_schema(event_schema):
    valid_event = {
        "schema_version": "phase34-event-v1",
        "event_id": "evt_001",
        "event_type": "phase34.step.completed",
        "run_id": "run_001",
        "case_id": "case_01",
        "timestamp": "2026-09-03T12:00:00Z",
        "phase": "PHASE_3",
        "step": "debate_round_1",
        "status": "SUCCESS",
        "reason_code": None,
        "reason": None,
        "payload": {"agreement": 1.0},
        "integrity_hash": "hash_evt_001"
    }
    validate(instance=valid_event, schema=event_schema)

def test_invalid_event_schema(event_schema):
    invalid_event = {
        "schema_version": "phase34-event-v1",
        "event_id": "evt_001",
        "event_type": "phase34.step.completed",
        "run_id": "run_001",
        "case_id": "case_01",
        "timestamp": "2026-09-03T12:00:00Z",
        "phase": "INVALID_PHASE",
        "step": "debate_round_1",
        "status": "SUCCESS",
        "reason_code": None,
        "reason": None,
        "payload": {},
        "integrity_hash": None
    }
    with pytest.raises(ValidationError):
        validate(instance=invalid_event, schema=event_schema)
