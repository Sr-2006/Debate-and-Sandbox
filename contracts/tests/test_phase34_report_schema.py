import json
from pathlib import Path
from datetime import datetime
import pytest
from jsonschema import Draft7Validator, FormatChecker

CONTRACTS_DIR = Path(__file__).parent.parent

def get_format_checker() -> FormatChecker:
    fc = FormatChecker()
    @fc.checks("date-time")
    def check_datetime(val):
        if not isinstance(val, str):
            return True
        try:
            if "T" not in val:
                return False
            datetime.fromisoformat(val.replace("Z", "+00:00"))
            return True
        except Exception:
            return False
    return fc

def load_schema(schema_name: str) -> dict:
    schema_path = CONTRACTS_DIR / schema_name
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)

def validate_instance(instance: dict, schema: dict):
    Draft7Validator.check_schema(schema)
    validator = Draft7Validator(schema, format_checker=get_format_checker())
    errors = sorted(validator.iter_errors(instance), key=lambda e: e.path)
    if errors:
        raise ValueError(f"Schema validation failed with errors: {[e.message for e in errors]}")

@pytest.fixture
def report_schema():
    return load_schema("phase34_report_v1.schema.json")

@pytest.fixture
def event_schema():
    return load_schema("phase34_event_v1.schema.json")

@pytest.fixture
def valid_simulation_report():
    return {
        "schema_version": "phase34-report-v1",
        "report_type": "PHASE34_PROBLEM_SUMMARY",
        "run": {
            "verification_run_id": "verif_001",
            "problem_run_id": "prob_001",
            "commit_sha": "78ab8a379af77a7fc35175e72621d06227afdb6a",
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
                "threshold": 0.80,
                "uncertainty": 0.05,
                "calibration_status": "CALIBRATED",
                "evidence_count": 3,
                "component_agreement": 1.0,
                "evidence_grounding": 0.90,
                "veto_applied": False,
                "veto_cap": None,
                "reason_codes": ["DIAGNOSED"]
            },
            "safety": {"status": "PASS"},
            "selected_intent": {"intent_type": "postgres.setting.update"},
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
                "reason_code": "DIAGNOSED",
                "reason": "Target verified",
                "data": {"status": "running"},
                "duration_ms": 10
            },
            "before_observations": {
                "status": "COMPLETED",
                "attempted": True,
                "reason_code": "DIAGNOSED",
                "reason": "Pre-state captured",
                "data": {},
                "duration_ms": 50
            },
            "fault_setup": {
                "status": "COMPLETED",
                "attempted": True,
                "reason_code": "DIAGNOSED",
                "reason": "Fault injected",
                "data": {},
                "duration_ms": 20
            },
            "execution": {
                "status": "SUCCESS",
                "attempted": True,
                "capability": "postgres.setting.update",
                "mode": "MUTATE_REVERSIBLE",
                "parameters": {"setting_name": "max_connections", "value": "200"},
                "result": {"output": "ok"},
                "duration_ms": 100
            },
            "after_observations": {
                "status": "COMPLETED",
                "attempted": True,
                "reason_code": "DIAGNOSED",
                "reason": "Post-state captured",
                "data": {},
                "duration_ms": 50
            },
            "verification": {
                "status": "PASSED",
                "attempted": True,
                "reason_code": "VERIFIED_RECOVERED",
                "reason": "Postcondition verified",
                "data": {},
                "duration_ms": 30
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
                "reason": "Cleanup done",
                "data": {},
                "duration_ms": 10
            },
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

# 1. Valid complete simulation report
def test_valid_complete_simulation_report(report_schema, valid_simulation_report):
    validate_instance(valid_simulation_report, report_schema)

# 2. Valid blocked/NOT_RUN report
def test_valid_blocked_not_run_report(report_schema, valid_simulation_report):
    rep = valid_simulation_report.copy()
    rep["phase_4"]["execution"] = {
        "status": "NOT_RUN",
        "attempted": False,
        "capability": "postgres.setting.update",
        "mode": "MUTATE_HIGH_RISK",
        "parameters": {},
        "result": {"reason": "High risk action requires human review"},
        "duration_ms": 0
    }
    rep["phase_4"]["attestation"] = {
        "status": "NOT_RUN",
        "attempted": False,
        "reason_code": "BLOCKED_SAFETY",
        "reason": "Stage blocked by safety gate",
        "data": {},
        "duration_ms": 0
    }
    validate_instance(rep, report_schema)

# 3. Missing top-level field rejected
def test_missing_top_level_field_rejected(report_schema, valid_simulation_report):
    rep = valid_simulation_report.copy()
    del rep["integrity"]
    with pytest.raises(ValueError):
        validate_instance(rep, report_schema)

# 4. Extra top-level field rejected
def test_extra_top_level_field_rejected(report_schema, valid_simulation_report):
    rep = valid_simulation_report.copy()
    rep["extra_field"] = "not_allowed"
    with pytest.raises(ValueError):
        validate_instance(rep, report_schema)

# 5. Extra nested field rejected
def test_extra_nested_field_rejected(report_schema, valid_simulation_report):
    rep = valid_simulation_report.copy()
    rep["run"]["extra_nested"] = "not_allowed"
    with pytest.raises(ValueError):
        validate_instance(rep, report_schema)

# 6. Null stage object rejected
def test_null_stage_object_rejected(report_schema, valid_simulation_report):
    rep = valid_simulation_report.copy()
    rep["phase_4"]["rollback"] = None
    with pytest.raises(ValueError):
        validate_instance(rep, report_schema)

# 7. Malformed timestamp rejected
def test_malformed_timestamp_rejected(report_schema, valid_simulation_report):
    rep = valid_simulation_report.copy()
    rep["run"]["started_at"] = "invalid-date-format"
    with pytest.raises(ValueError):
        validate_instance(rep, report_schema)

# 8. Confidence outside 0–1 rejected
def test_confidence_outside_range_rejected(report_schema, valid_simulation_report):
    rep = valid_simulation_report.copy()
    rep["phase_3"]["confidence"]["score"] = 1.5
    with pytest.raises(ValueError):
        validate_instance(rep, report_schema)

# 9. Invalid RL recommendation rejected
def test_invalid_rl_recommendation_rejected(report_schema, valid_simulation_report):
    rep = valid_simulation_report.copy()
    rep["rl_advisory"]["recommendation"] = "INVALID_REC"
    with pytest.raises(ValueError):
        validate_instance(rep, report_schema)

# 10. influence_allowed=true rejected
def test_influence_allowed_true_rejected(report_schema, valid_simulation_report):
    rep = valid_simulation_report.copy()
    rep["rl_advisory"]["influence_allowed"] = True
    with pytest.raises(ValueError):
        validate_instance(rep, report_schema)

# 11. Simulation with eligible=true rejected
def test_simulation_eligible_true_rejected(report_schema, valid_simulation_report):
    rep = valid_simulation_report.copy()
    rep["learning"]["eligible"] = True
    with pytest.raises(ValueError):
        validate_instance(rep, report_schema)

# 12. Simulation with non-null reward rejected
def test_simulation_non_null_reward_rejected(report_schema, valid_simulation_report):
    rep = valid_simulation_report.copy()
    rep["learning"]["reward"] = 1.0
    with pytest.raises(ValueError):
        validate_instance(rep, report_schema)

# 13. Simulation with nonzero sample weight rejected
def test_simulation_nonzero_sample_weight_rejected(report_schema, valid_simulation_report):
    rep = valid_simulation_report.copy()
    rep["learning"]["sample_weight"] = 1.0
    with pytest.raises(ValueError):
        validate_instance(rep, report_schema)

# Event Schema Tests
@pytest.fixture
def valid_event():
    return {
        "schema_version": "phase34-event-v1",
        "sequence": 1,
        "timestamp": "2026-09-03T12:00:00Z",
        "verification_run_id": "verif_001",
        "problem_run_id": "prob_001",
        "case_id": "case_01",
        "phase": "PHASE_3",
        "component": "debate_manager",
        "event": "round_completed",
        "status": "SUCCESS",
        "reason_code": "DIAGNOSED",
        "duration_ms": 1500,
        "details": {"agreement": 1.0}
    }

# 14. Valid event accepted
def test_valid_event_accepted(event_schema, valid_event):
    validate_instance(valid_event, event_schema)

# 15. Event without sequence rejected
def test_event_without_sequence_rejected(event_schema, valid_event):
    evt = valid_event.copy()
    del evt["sequence"]
    with pytest.raises(ValueError):
        validate_instance(evt, event_schema)

# 16. Event sequence zero rejected
def test_event_sequence_zero_rejected(event_schema, valid_event):
    evt = valid_event.copy()
    evt["sequence"] = 0
    with pytest.raises(ValueError):
        validate_instance(evt, event_schema)

# 17. Event with extra property rejected
def test_event_extra_property_rejected(event_schema, valid_event):
    evt = valid_event.copy()
    evt["extra_prop"] = "invalid"
    with pytest.raises(ValueError):
        validate_instance(evt, event_schema)

# 18. Event with malformed timestamp rejected
def test_event_malformed_timestamp_rejected(event_schema, valid_event):
    evt = valid_event.copy()
    evt["timestamp"] = "bad-timestamp"
    with pytest.raises(ValueError):
        validate_instance(evt, event_schema)
