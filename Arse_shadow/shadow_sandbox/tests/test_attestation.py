from unittest.mock import MagicMock, patch
import pytest
import docker.errors
from shadow_sandbox.attestation import attest_shadow_environment
from shadow_sandbox.run_pipeline import check_attestation, run_phase4_pipeline
from contracts.reason_codes import ReasonCode

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
def test_high_risk_action_skips_attestation(mock_docker):
    v2_envelope = {
        "incident_id": "case_test_hr",
        "intents": [{
            "intent_id": "int_hr",
            "intent_type": "workload.restart",
            "mode": "MUTATE_HIGH_RISK",
            "target_ref": {"kind": "container", "canonical_name": "postgres-db"},
            "parameters": {},
            "requires_human_approval": True
        }],
        "phase3_confidence": {"score": 0.9}
    }
    res = run_phase4_pipeline(v2_envelope, is_simulated=False)
    assert res["status"] == "HUMAN_REVIEW_REQUIRED"
    assert res["attestation"]["attempted"] is False
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
