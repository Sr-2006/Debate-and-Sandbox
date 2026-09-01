import pytest
from shadow_sandbox.attestation import attest_shadow_environment
from contracts.reason_codes import ReasonCode

def test_attestation_valid_shadow_target():
    ok, code, msg = attest_shadow_environment("shadow-postgres-db")
    assert ok is True
    assert code == ReasonCode.DIAGNOSED

def test_attestation_invalid_prod_target():
    ok, code, msg = attest_shadow_environment("prod-database")
    assert ok is False
    assert code == ReasonCode.ATTESTATION_FAILED
