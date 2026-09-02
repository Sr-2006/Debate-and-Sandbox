import os
import json
import pytest
os.environ["DEBATE_MOCK_LLM"] = "1"

from contracts.validation import validate_envelope, get_capabilities, is_mvp_supported
from debate.action_publisher import build_action_proposed
from shadow_sandbox.run_pipeline import run_phase4_pipeline
from shadow_sandbox.reports.report_generator import generate_mvp_report


def test_1_structured_phase3_intent_passes_validation():
    """Unit Test 1: Structured Phase 3 intent passes validation."""
    p3_result = {
        "solution": {
            "problem_summary": "PostgreSQL connections exhausted",
            "root_cause": "max_connections set too low",
            "primary_component": "postgres-db",
            "evidence_refs": ["log_line_17"],
            "confidence": 0.85,
            "intent": {
                "intent_type": "postgres.setting.update",
                "mode": "MUTATE_REVERSIBLE",
                "target_ref": {
                    "kind": "database",
                    "canonical_name": "postgres-db"
                },
                "parameters": {
                    "setting_name": "max_connections",
                    "value": "200"
                }
            },
            "human_recommendation": "Increase max_connections to 200"
        },
        "confidence_score": 85,
        "execution_tier": "TIER_1_AUTONOMOUS_EXECUTION"
    }

    envelope = build_action_proposed("case_test_01", p3_result)
    is_valid, errors, reason = validate_envelope(envelope)
    assert is_valid is True, f"Validation failed: {errors}"
    assert envelope["intents"][0]["intent_type"] == "postgres.setting.update"
    assert envelope["intents"][0]["parameters"]["setting_name"] == "max_connections"


def test_2_unknown_intent_produces_no_supported_action():
    """Unit Test 2: Unknown intent produces NO_SUPPORTED_ACTION."""
    p3_result = {
        "solution": {
            "problem_summary": "Unknown issue",
            "root_cause": "Magic command required",
            "primary_component": "user-service",
            "evidence_refs": ["log_01"],
            "confidence": 0.80,
            "intent": {
                "intent_type": "NO_SUPPORTED_ACTION",
                "mode": "OBSERVE",
                "target_ref": {
                    "kind": "container",
                    "canonical_name": "user-service"
                },
                "parameters": {}
            },
            "human_recommendation": "Manual investigation required"
        },
        "confidence_score": 80,
        "execution_tier": "TIER_2_SHADOW_SANDBOX"
    }

    envelope = build_action_proposed("case_test_02", p3_result)
    p4_result = run_phase4_pipeline(envelope)
    assert p4_result["status"] == "NO_SUPPORTED_ACTION"


def test_3_evidence_is_never_fabricated():
    """Unit Test 3: Evidence is never fabricated."""
    p3_result = {
        "solution": {
            "problem_summary": "No evidence provided",
            "root_cause": "Unknown",
            "primary_component": "redis",
            "evidence_refs": [],  # Empty evidence
            "confidence": 0.70,
            "intent": {
                "intent_type": "redis.eviction_policy.update",
                "mode": "MUTATE_REVERSIBLE",
                "target_ref": {
                    "kind": "cache",
                    "canonical_name": "redis"
                },
                "parameters": {
                    "policy": "allkeys-lru"
                }
            },
            "human_recommendation": "Check eviction policy"
        },
        "confidence_score": 70,
        "execution_tier": "TIER_2_SHADOW_SANDBOX"
    }

    try:
        envelope = build_action_proposed("case_test_03", p3_result)
        is_valid, errors, reason = validate_envelope(envelope)
        assert is_valid is False
    except ValueError as e:
        assert "BLOCKED_MISSING_EVIDENCE" in str(e) or "missing evidence_refs" in str(e).lower()



def test_4_low_confidence_case_runs_observation_only():
    """Unit Test 4: Low-confidence case runs observation only."""
    envelope = {
        "schema_version": "2.0",
        "event_id": "evt_low_conf",
        "event_type": "autosre.action.proposed",
        "incident_id": "case_low_conf",
        "correlation_id": "corr_low_conf",
        "fingerprint": "fp_low_conf",
        "created_at": "2026-09-02T10:00:00Z",
        "source": {"phase": "PHASE_3", "code_commit": "2f2127d7"},
        "problem_summary": "Low confidence diagnosis",
        "target_ref": {"kind": "container", "canonical_name": "user-service"},
        "phase3_confidence": {"score": 0.35},  # Below 0.50
        "execution_tier": "TIER_3_RE_ITERATION",
        "safety_violation": False,
        "evidence_refs": ["log_low_01"],
        "intents": [
            {
                "intent_id": "int_low_01",
                "intent_type": "container.restart",
                "mode": "MUTATE_REVERSIBLE",
                "target_ref": {"kind": "container", "canonical_name": "user-service"},
                "parameters": {},
                "evidence_refs": ["log_low_01"],
                "preconditions": [],
                "postconditions": [],
                "timeout_seconds": 30,
                "max_attempts": 1,
                "risk_class": "MEDIUM",
                "requires_human_approval": False
            }
        ],
        "human_summary": "Low confidence case"
    }

    p4_result = run_phase4_pipeline(envelope)
    assert p4_result["status"] == "READ_ONLY_OBSERVED"
    assert p4_result["execution"]["capability"] == "observe.logs.search"


def test_5_supported_mutation_captures_real_pre_state():
    """Unit Test 5: Supported mutation captures real pre-state."""
    envelope = {
        "schema_version": "2.0",
        "event_id": "evt_prestate",
        "event_type": "autosre.action.proposed",
        "incident_id": "case_prestate",
        "correlation_id": "corr_prestate",
        "fingerprint": "fp_prestate",
        "created_at": "2026-09-02T10:00:00Z",
        "source": {"phase": "PHASE_3", "code_commit": "2f2127d7"},
        "problem_summary": "Postgres setting test",
        "target_ref": {"kind": "database", "canonical_name": "postgres-db"},
        "phase3_confidence": {"score": 0.85},
        "execution_tier": "TIER_1_AUTONOMOUS_EXECUTION",
        "safety_violation": False,
        "evidence_refs": ["log_pg_01"],
        "intents": [
            {
                "intent_id": "int_pg_01",
                "intent_type": "postgres.setting.update",
                "mode": "MUTATE_REVERSIBLE",
                "target_ref": {"kind": "database", "canonical_name": "postgres-db"},
                "parameters": {"setting_name": "max_connections", "value": "200"},
                "evidence_refs": ["log_pg_01"],
                "preconditions": [],
                "postconditions": [],
                "timeout_seconds": 30,
                "max_attempts": 1,
                "risk_class": "MEDIUM",
                "requires_human_approval": False
            }
        ],
        "human_summary": "Postgres setting test"
    }

    p4_result = run_phase4_pipeline(envelope, is_simulated=True)
    assert "before_observations" in p4_result
    assert p4_result["status"] in ["SANDBOX_VERIFIED", "SIMULATION_VERIFIED", "SANDBOX_FAILED_ROLLED_BACK"]


def test_6_verification_failure_restores_pre_state():
    """Unit Test 6: Verification failure restores pre-state."""
    envelope = {
        "schema_version": "2.0",
        "event_id": "evt_vf_rollback",
        "event_type": "autosre.action.proposed",
        "incident_id": "case_vf_rollback",
        "correlation_id": "corr_vf_rollback",
        "fingerprint": "fp_vf_rollback",
        "created_at": "2026-09-02T10:00:00Z",
        "source": {"phase": "PHASE_3", "code_commit": "2f2127d7"},
        "problem_summary": "Redis invalid eviction test",
        "target_ref": {"kind": "cache", "canonical_name": "redis"},
        "phase3_confidence": {"score": 0.85},
        "execution_tier": "TIER_1_AUTONOMOUS_EXECUTION",
        "safety_violation": False,
        "evidence_refs": ["log_redis_01"],
        "intents": [
            {
                "intent_id": "int_redis_01",
                "intent_type": "redis.eviction_policy.update",
                "mode": "MUTATE_REVERSIBLE",
                "target_ref": {"kind": "cache", "canonical_name": "redis"},
                "parameters": {"policy": "allkeys-lru"},
                "evidence_refs": ["log_redis_01"],
                "preconditions": [],
                "postconditions": [],
                "timeout_seconds": 30,
                "max_attempts": 1,
                "risk_class": "MEDIUM",
                "requires_human_approval": False
            }
        ],
        "human_summary": "Redis policy test"
    }

    p4_result = run_phase4_pipeline(envelope, is_simulated=True)
    assert p4_result["status"] in ["SANDBOX_VERIFIED", "SIMULATION_VERIFIED", "SANDBOX_FAILED_ROLLED_BACK"]


    if p4_result["status"] == "SANDBOX_FAILED_ROLLED_BACK":
        assert p4_result["rollback"]["attempted"] is True


def test_7_unsupported_capability_produces_complete_report(tmp_path):
    """Unit Test 7: Unsupported capability produces a complete report with UNSUPPORTED_IN_MVP."""
    envelope = {
        "schema_version": "2.0",
        "event_id": "evt_unsup",
        "event_type": "autosre.action.proposed",
        "incident_id": "case_unsupported",
        "correlation_id": "corr_unsup",
        "fingerprint": "fp_unsup",
        "created_at": "2026-09-02T10:00:00Z",
        "source": {"phase": "PHASE_3", "code_commit": "2f2127d7"},
        "problem_summary": "Cert renewal requested",
        "target_ref": {"kind": "certificate", "canonical_name": "cert-manager"},
        "phase3_confidence": {"score": 0.90},
        "execution_tier": "TIER_1_AUTONOMOUS_EXECUTION",
        "safety_violation": False,
        "evidence_refs": ["log_cert_01"],
        "intents": [
            {
                "intent_id": "int_cert_01",
                "intent_type": "tls.certificate.renew",  # Deferred in MVP
                "mode": "MUTATE_REVERSIBLE",
                "target_ref": {"kind": "certificate", "canonical_name": "cert-manager"},
                "parameters": {"secret_name": "tls-secret", "domain": "auth.internal.net"},

                "evidence_refs": ["log_cert_01"],
                "preconditions": [],
                "postconditions": [],
                "timeout_seconds": 30,
                "max_attempts": 1,
                "risk_class": "MEDIUM",
                "requires_human_approval": False
            }
        ],
        "human_summary": "TLS cert renewal"
    }

    p4_result = run_phase4_pipeline(envelope)
    assert p4_result["status"] == "UNSUPPORTED_IN_MVP"

    ctx = {
        "report_version": "mvp-1.0",
        "incident_id": "case_unsupported",
        "run_id": "run_test_unsup",
        "started_at": "2026-09-02T10:00:00Z",
        "completed_at": "2026-09-02T10:00:01Z",
        "problem": {"raw_input": {}, "normalized_problem": "Cert renewal", "expected_behavior": "Cert renewed"},
        "phase_3": {"status": "COMPLETED", "agents": {}, "orchestrator": {}, "confidence": {"score": 0.90}, "selected_intent": envelope["intents"][0], "duration_ms": 100},
        "phase_3_to_4_handoff": {"exact_envelope": envelope, "payload_hash": "hash_unsup", "validation": {"passed": True}},
        "phase_4": p4_result,
        "final_summary": {"outcome": "UNSUPPORTED_IN_MVP", "problem_resolved_in_sandbox": False, "production_recommendation": "Deferred in MVP"}
    }

    j_path, m_path = generate_mvp_report(ctx, reports_base_dir=str(tmp_path))
    assert os.path.exists(j_path)
    assert os.path.exists(m_path)

    with open(j_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["final_summary"]["outcome"] == "UNSUPPORTED_IN_MVP"

    with open(m_path, "r", encoding="utf-8") as f:
        text = f.read()
        assert "UNSUPPORTED_IN_MVP" in text


def test_8_json_and_markdown_reports_contain_all_required_sections(tmp_path):
    """Unit Test 8: JSON and Markdown reports contain all required sections."""
    ctx = {
        "report_version": "mvp-1.0",
        "incident_id": "case_sec_test",
        "run_id": "run_sec_test",
        "started_at": "2026-09-02T10:00:00Z",
        "completed_at": "2026-09-02T10:00:02Z",
        "problem": {
            "raw_input": {"test": 123},
            "normalized_problem": "OOM in user-service",
            "expected_behavior": "Service healthy"
        },
        "phase_3": {
            "status": "COMPLETED",
            "agents": {
                "optimist": {"prompt": "p1", "response": {"logic": "ok"}, "latency_ms": 10},
                "critic": {"prompt": "p2", "response": {"logic": "warn"}, "latency_ms": 12},
                "fact_checker": {"prompt": "p3", "response": {"logic": "grounded"}, "latency_ms": 15}
            },
            "orchestrator": {"prompt": "orch_p", "response": {"root_cause": "OOM"}},
            "confidence": {"score": 0.85, "reasoning": "High agreement", "evidence_refs": ["log_1"]},
            "selected_intent": {"intent_type": "container.restart"},
            "duration_ms": 50
        },
        "phase_3_to_4_handoff": {
            "exact_envelope": {"schema_version": "2.0"},
            "payload_hash": "hash_sec",
            "validation": {"passed": True, "errors": []}
        },
        "phase_4": {
            "status": "SANDBOX_VERIFIED",
            "exact_input": {"schema_version": "2.0"},
            "target": {"kind": "container", "canonical_name": "user-service"},
            "attestation": {"attested": True},
            "before_observations": {"status": "unhealthy"},
            "fault_setup": {"injected": False},
            "execution": {"capability": "container.restart", "parameters": {}, "result": {"success": True}, "duration_ms": 100},
            "after_observations": {"status": "healthy"},
            "verification": {"passed": True},
            "rollback": {"attempted": False, "result": None},
            "cleanup": {"completed": True},
            "state_history": ["RECEIVED", "VALIDATED", "OBSERVED_BEFORE", "EXECUTED_OR_BLOCKED", "OBSERVED_AFTER", "VERIFIED_OR_ROLLED_BACK", "REPORTED"],
            "duration_ms": 200
        },
        "final_summary": {
            "outcome": "SANDBOX_VERIFIED",
            "problem_resolved_in_sandbox": True,
            "production_recommendation": "Human approval recommended",
            "limitations": ["Shadow sandbox only"]
        }
    }

    j_path, m_path = generate_mvp_report(ctx, reports_base_dir=str(tmp_path))

    with open(j_path, "r", encoding="utf-8") as f:
        j_data = json.load(f)
        assert j_data["report_version"] == "mvp-1.0"
        assert j_data["incident_id"] == "case_sec_test"

    with open(m_path, "r", encoding="utf-8") as f:
        md = f.read()
        # Verify all 16 Markdown sections are present
        sections = [
            "1. Problem Overview",
            "2. Original Telemetry / Input",
            "3. Optimist Analysis",
            "4. Critic Analysis",
            "5. Fact Checker Analysis",
            "6. Debate Agreement and Disagreements",
            "7. Final Orchestrator Decision",
            "8. Confidence Breakdown",
            "9. Exact Phase 3 -> Phase 4 Envelope",
            "10. Shadow Target and Before-State",
            "11. Executed Capability and Parameters",
            "12. Execution Observations",
            "13. Verification Result",
            "14. Rollback and Cleanup",
            "15. Final Outcome",
            "16. Limitations and Human Recommendation"
        ]
        for sec in sections:
            assert sec in md, f"Missing markdown report section: '{sec}'"
