"""Tests for PostgreSQL executor out-of-transaction execution and rollback semantics."""

import pytest
from unittest.mock import patch, MagicMock
from shadow_sandbox.remediation.executors.postgres_executor import PostgresExecutor, ALLOWED_SETTINGS
from shadow_sandbox.remediation.verifiers.postgres_verifier import PostgresVerifier
from shadow_sandbox.run_pipeline import run_phase4_pipeline
from shadow_sandbox.attestation import attest_shadow_environment
from debate.action_publisher import build_action_proposed


def _make_valid_envelope(incident_id: str = "test_inc_1") -> dict:
    debate_result = {
        "solution": {
            "primary_component": "postgres-db",
            "intents": [{
                "intent_type": "postgres.setting.update",
                "mode": "MUTATE_REVERSIBLE",
                "target_ref": {"kind": "container", "canonical_name": "postgres-db"},
                "parameters": {"setting_name": "max_connections", "value": "200"},
                "evidence_refs": ["FATAL: remaining connection slots reserved"]
            }],
            "confidence": 90,
            "evidence_anchors": ["FATAL: remaining connection slots reserved"]
        },
        "confidence_score": 0.90,
        "phase3_status": "COMPLETED",
        "execution_tier": "TIER_1_AUTONOMOUS_EXECUTION",
        "safety_violation": False,
        "evidence_grounding": ["FATAL: remaining connection slots reserved"],
        "original_problem": {
            "incident_event": {"incident_id": incident_id, "target_service": "postgres-db"}
        }
    }
    return build_action_proposed(incident_id, debate_result)


def test_postgres_executor_alter_system_out_of_transaction():
    """Verify ALTER SYSTEM executes with separate -c statements to avoid implicit multi-statement transaction block."""
    executor = PostgresExecutor()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ALTER SYSTEM\nt", stderr="")
        res = executor.execute("shadow-postgres-db", "postgres.setting.update", {"setting_name": "max_connections", "value": "200"})
        assert res["success"] is True
        assert "SUCCESS" in res["output"]

        # Assert cmd structure uses separate -c arguments
        called_cmd = mock_run.call_args[0][0]
        assert "docker" in called_cmd
        assert "exec" in called_cmd
        assert "psql" in called_cmd
        assert "-c" in called_cmd
        c_indices = [i for i, arg in enumerate(called_cmd) if arg == "-c"]
        assert len(c_indices) >= 2
        assert "ALTER SYSTEM SET max_connections = '200';" in called_cmd
        assert "SELECT pg_reload_conf();" in called_cmd


def test_postgres_executor_disallowed_setting_blocked():
    """Verify settings not in ALLOWED_SETTINGS allowlist are rejected."""
    executor = PostgresExecutor()
    res = executor.execute("shadow-postgres-db", "postgres.setting.update", {"setting_name": "malicious_setting; DROP TABLE users;", "value": "1"})
    assert res["success"] is False
    assert "not in allowed settings allowlist" in res["output"]


def test_postgres_executor_invalid_value_format_blocked():
    """Verify values failing regex format validation are rejected."""
    executor = PostgresExecutor()
    res = executor.execute("shadow-postgres-db", "postgres.setting.update", {"setting_name": "max_connections", "value": "200; DROP TABLE users;"})
    assert res["success"] is False
    assert "failed strict format validation" in res["output"]


def test_failed_execution_before_mutation_no_rollback_failure():
    """Verify when execution fails before mutation, rollback is marked NOT_REQUIRED and not ROLLBACK_FAILED."""
    envelope = _make_valid_envelope("test_inc_failed_exec")

    mock_exec = MagicMock()
    def side_effect(target, action, params):
        if action == "postgres.setting.read":
            return {"success": True, "output": "SUCCESS: 100"}
        elif action == "postgres.setting.update":
            return {"success": False, "output": "ERROR: mock execution failure"}
        return {"success": False, "output": "ERROR"}

    mock_exec.execute.side_effect = side_effect

    mock_ver = MagicMock()
    mock_ver.verify.return_value = {"passed": False, "reason": "Execution failed before verification"}

    with patch("shadow_sandbox.run_pipeline.check_attestation", return_value={"attested": True, "status": "PASSED"}), \
         patch("shadow_sandbox.run_pipeline.get_executor", return_value=mock_exec), \
         patch("shadow_sandbox.run_pipeline.get_verifier", return_value=mock_ver):

        res = run_phase4_pipeline(envelope, is_simulated=False)
        assert res["status"] in ["SANDBOX_EXECUTION_FAILED", "SIMULATION_EXECUTION_FAILED"]
        assert res["rollback"]["attempted"] is False
        assert res["rollback"]["status"] == "NOT_REQUIRED"
        assert "not required" in res["rollback"]["reason"]


def test_rollback_restores_previous_value_when_verification_fails():
    """Verify rollback restores previous value if execution succeeded but verification failed."""
    envelope = _make_valid_envelope("test_inc_rb_restore")

    recorded_updates = []
    mock_exec = MagicMock()

    def side_effect(target, action, params):
        if action == "postgres.setting.read":
            return {"success": True, "output": "SUCCESS: 100"}
        elif action == "postgres.setting.update":
            recorded_updates.append(params.get("value"))
            return {"success": True, "output": f"SUCCESS: updated to {params.get('value')}"}
        return {"success": False}

    mock_exec.execute.side_effect = side_effect

    mock_ver = MagicMock()
    mock_ver.verify.return_value = {"passed": False, "reason": "Verification failed"}

    with patch("shadow_sandbox.run_pipeline.check_attestation", return_value={"attested": True, "status": "PASSED"}), \
         patch("shadow_sandbox.run_pipeline.get_executor", return_value=mock_exec), \
         patch("shadow_sandbox.run_pipeline.get_verifier", return_value=mock_ver):

        res = run_phase4_pipeline(envelope, is_simulated=False)
        assert res["status"] in ["SANDBOX_FAILED_ROLLED_BACK", "SIMULATION_FAILED_ROLLED_BACK"]
        assert res["rollback"]["attempted"] is True
        # First execution was 200, rollback was 100
        assert recorded_updates == ["200", "100"]


def test_attestation_required_for_shadow_postgres():
    """Verify attestation failure blocks execution immediately."""
    envelope = _make_valid_envelope("test_inc_attest_fail")

    with patch("shadow_sandbox.run_pipeline.check_attestation", return_value={"attested": False, "status": "FAILED"}):
        res = run_phase4_pipeline(envelope, is_simulated=False)
        assert res["status"] == "ATTESTATION_FAILED"
        assert res["execution"]["attempted"] is False
