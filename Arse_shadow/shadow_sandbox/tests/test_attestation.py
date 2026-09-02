from unittest.mock import MagicMock, patch
import pytest
from shadow_sandbox.attestation import attest_shadow_environment
from contracts.reason_codes import ReasonCode

@patch("docker.from_env")
def test_attestation_valid_shadow_target(mock_docker):
    mock_container = MagicMock()
    mock_docker.return_value.containers.get.return_value = mock_container
    ok, code, msg = attest_shadow_environment("shadow-postgres-db")
    assert ok is True
    assert code == ReasonCode.DIAGNOSED

def test_attestation_invalid_prod_target():
    ok, code, msg = attest_shadow_environment("prod-database")
    assert ok is False
    assert code == ReasonCode.ATTESTATION_FAILED

@patch("docker.from_env")
def test_attestation_missing_container(mock_docker):
    mock_docker.return_value.containers.get.side_effect = Exception("Container not found")
    ok, code, msg = attest_shadow_environment("shadow-missing-service")
    assert ok is False
    assert code == ReasonCode.ATTESTATION_FAILED

