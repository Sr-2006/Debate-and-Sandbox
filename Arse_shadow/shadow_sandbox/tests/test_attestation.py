from unittest.mock import MagicMock, patch
import pytest
import docker.errors
from shadow_sandbox.attestation import attest_shadow_environment
from shadow_sandbox.run_pipeline import check_attestation, run_phase4_pipeline
from contracts.reason_codes import ReasonCode

def make_valid_v2_envelope() -> dict:
    return {
        "schema_version": "2.0",
        "event_id": "evt_attestation_test",
        "event_type": "autosre.action.proposed",
        "incident_id": "attestation_test",
        "correlation_id": "corr_attestation_test",
        "fingerprint": "fp_attestation_test",
        "created_at": "2026-09-03T12:00:00Z",
        "source": {
            "phase": "PHASE_3",
            "code_commit": "test"
        },
        "problem_summary": "Attestation safety test",
        "target_ref": {
            "kind": "container",
            "canonical_name": "postgres-db",
            "namespace": "shadow"
        },
        "phase3_confidence": {
            "score": 0.90,
            "uncertainty": 0.10,
            "calibration_status": "UNCALIBRATED",
            "calibration_version": "test"
        },
        "execution_tier": "tier_1",
        "safety_violation": False,
        "evidence_refs": ["test_evidence"],
        "intents": [
            {
                "intent_id": "intent_attestation_test",
                "intent_type": "container.restart",
                "mode": "MUTATE_REVERSIBLE",
                "target_ref": {
                    "kind": "container",
                    "canonical_name": "postgres-db",
                    "namespace": "shadow"
                },
                "parameters": {},
                "evidence_refs": ["test_evidence"],
                "preconditions": [],
                "postconditions": [],
                "timeout_seconds": 30,
                "max_attempts": 1,
                "risk_class": "MEDIUM",
                "requires_human_approval": False
            }
        ],
        "human_summary": "Attestation safety test"
    }

# 1. Missing mandatory label fails
@patch("docker.from_env")
def test_attestation_missing_mandatory_label(mock_docker):
    mock_container = MagicMock()
    mock_container.labels = {}  # missing autosre.environment
    mock_container.status = "running"
    mock_docker.return_value.containers.get.return_value = mock_container

    ok, code, msg = attest_shadow_environment("postgres-db", "container")
    assert ok is False
    assert code == ReasonCode.ATTESTATION_FAILED
    assert "missing mandatory label" in msg

# 2. Incorrect environment label fails
@patch("docker.from_env")
def test_attestation_incorrect_environment_label(mock_docker):
    mock_container = MagicMock()
    mock_container.labels = {"autosre.environment": "production"}
    mock_container.status = "running"
    mock_docker.return_value.containers.get.return_value = mock_container

    ok, code, msg = attest_shadow_environment("postgres-db", "container")
    assert ok is False
    assert code == ReasonCode.ATTESTATION_FAILED
    assert "label 'production' != 'shadow'" in msg

# 3. Stopped container fails
@patch("docker.from_env")
def test_attestation_stopped_container_fails(mock_docker):
    mock_container = MagicMock()
    mock_container.labels = {"autosre.environment": "shadow"}
    mock_container.status = "exited"
    mock_docker.return_value.containers.get.return_value = mock_container

    ok, code, msg = attest_shadow_environment("postgres-db", "container")
    assert ok is False
    assert code == ReasonCode.ATTESTATION_FAILED
    assert "status is 'exited', expected 'running'" in msg

# 4. Unhealthy container fails
@patch("docker.from_env")
def test_attestation_unhealthy_container_fails(mock_docker):
    mock_container = MagicMock()
    mock_container.labels = {"autosre.environment": "shadow"}
    mock_container.status = "running"
    mock_container.attrs = {"State": {"Health": {"Status": "unhealthy"}}}
    mock_docker.return_value.containers.get.return_value = mock_container

    ok, code, msg = attest_shadow_environment("postgres-db", "container")
    assert ok is False
    assert code == ReasonCode.ATTESTATION_FAILED
    assert "health status is 'unhealthy', expected 'healthy'" in msg

# 5. Running/healthy/labeled shadow container passes
@patch("docker.from_env")
def test_attestation_running_healthy_container_passes(mock_docker):
    mock_container = MagicMock()
    mock_container.labels = {"autosre.environment": "shadow"}
    mock_container.status = "running"
    mock_container.attrs = {"State": {"Health": {"Status": "healthy"}}}
    mock_docker.return_value.containers.get.return_value = mock_container

    ok, code, msg = attest_shadow_environment("postgres-db", "container")
    assert ok is True
    assert code == ReasonCode.DIAGNOSED
    assert "Attestation verified" in msg

# 6. Production marker fails
def test_attestation_production_marker_fails():
    ok, code, msg = attest_shadow_environment("prod-postgres-db", "container")
    assert ok is False
    assert code == ReasonCode.ATTESTATION_FAILED
    assert "production marker" in msg

# 7. Unresolved target fails
def test_attestation_unresolved_target_fails():
    ok, code, msg = attest_shadow_environment("", "container")
    assert ok is False
    assert code == ReasonCode.BLOCKED_TARGET_UNRESOLVED

# 8. Kubernetes target uses kubectl rather than Docker
@patch("subprocess.run")
@patch("docker.from_env")
def test_attestation_kubernetes_uses_kubectl(mock_docker, mock_subproc):
    mock_subproc.return_value.returncode = 0
    ok, code, msg = attest_shadow_environment("auth-service", "workload", {"namespace": "shadow"})

    assert ok is True
    assert mock_docker.call_count == 0
    assert mock_subproc.call_count == 1
    args, kwargs = mock_subproc.call_args
    assert "kubectl" in args[0]

# 9. Simulated mode performs no Docker/kubectl calls
@patch("subprocess.run")
@patch("docker.from_env")
def test_attestation_simulated_mode_no_infra_calls(mock_docker, mock_subproc):
    res = check_attestation("postgres-db", target_kind="container", is_simulated=True)
    assert res["attested"] is True
    assert mock_docker.call_count == 0
    assert mock_subproc.call_count == 0

# 10. High-risk action skips attestation
@patch("docker.from_env")
@patch.dict("os.environ", {"DEBATE_MOCK_LLM": "0"})
def test_high_risk_action_skips_attestation(mock_docker):
    envelope = make_valid_v2_envelope()
    envelope["safety_violation"] = True
    envelope["intents"][0]["intent_type"] = "node.drain"
    envelope["intents"][0]["mode"] = "MUTATE_HIGH_RISK"
    envelope["intents"][0]["target_ref"] = {"kind": "node", "canonical_name": "node-1", "namespace": "shadow"}
    envelope["intents"][0]["parameters"] = {"node_name": "node-1"}
    envelope["intents"][0]["risk_class"] = "CRITICAL"
    envelope["intents"][0]["requires_human_approval"] = True

    result = run_phase4_pipeline(envelope, is_simulated=False)

    assert result["status"] == "HUMAN_REVIEW_REQUIRED"
    assert result["attestation"]["attempted"] is False
    assert result["execution"]["attempted"] is False
    assert mock_docker.call_count == 0

# 11. Already-prefixed target is not double-prefixed
@patch("docker.from_env")
def test_already_prefixed_target_not_double_prefixed(mock_docker):
    mock_container = MagicMock()
    mock_container.labels = {"autosre.environment": "shadow"}
    mock_container.status = "running"
    mock_docker.return_value.containers.get.return_value = mock_container

    ok, code, msg = attest_shadow_environment("shadow-postgres-db", "container")
    assert ok is True
    mock_docker.return_value.containers.get.assert_called_with("shadow-postgres-db")

# 12. Pipeline-level target normalization test: shadow-postgres-db -> shadow-postgres-db (never shadow-shadow-postgres-db)
@patch("docker.from_env")
@patch.dict("os.environ", {"DEBATE_MOCK_LLM": "0"})
def test_pipeline_target_normalization_no_double_prefix(mock_docker):
    mock_container = MagicMock()
    mock_container.labels = {"autosre.environment": "shadow"}
    mock_container.status = "running"
    mock_docker.return_value.containers.get.return_value = mock_container

    envelope = make_valid_v2_envelope()
    envelope["target_ref"]["canonical_name"] = "shadow-postgres-db"
    envelope["intents"][0]["target_ref"]["canonical_name"] = "shadow-postgres-db"

    res = run_phase4_pipeline(envelope, is_simulated=False)
    assert res["attestation"]["target"] == "shadow-postgres-db"
    mock_docker.return_value.containers.get.assert_called_with("shadow-postgres-db")

# 13. High-risk + low confidence priority test -> HUMAN_REVIEW_REQUIRED
@patch("docker.from_env")
@patch.dict("os.environ", {"DEBATE_MOCK_LLM": "0"})
def test_high_risk_and_low_confidence_priority(mock_docker):
    envelope = make_valid_v2_envelope()
    envelope["safety_violation"] = True
    envelope["intents"][0]["intent_type"] = "node.drain"
    envelope["intents"][0]["mode"] = "MUTATE_HIGH_RISK"
    envelope["intents"][0]["risk_class"] = "HIGH"
    envelope["intents"][0]["target_ref"] = {"kind": "node", "canonical_name": "node-1", "namespace": "shadow"}
    envelope["intents"][0]["parameters"] = {"node_name": "node-1"}
    envelope["intents"][0]["requires_human_approval"] = True
    envelope["phase3_confidence"]["score"] = 0.20  # Low confidence

    result = run_phase4_pipeline(envelope, is_simulated=False)
    assert result["status"] == "HUMAN_REVIEW_REQUIRED"
    assert result["attestation"]["attempted"] is False
    assert result["execution"]["attempted"] is False
    assert mock_docker.call_count == 0

# 14. Low-confidence failed attestation test -> ATTESTATION_FAILED before executor call
@patch("docker.from_env")
@patch.dict("os.environ", {"DEBATE_MOCK_LLM": "0"})
def test_low_confidence_failed_attestation_blocks_executor(mock_docker):
    mock_docker.return_value.containers.get.side_effect = docker.errors.NotFound("Container not found")

    envelope = make_valid_v2_envelope()
    envelope["phase3_confidence"]["score"] = 0.20  # Low confidence

    result = run_phase4_pipeline(envelope, is_simulated=False)
    assert result["status"] == "ATTESTATION_FAILED"
    assert result["attestation"]["attempted"] is True
    assert result["attestation"]["attested"] is False
    assert result["execution"]["attempted"] is False
