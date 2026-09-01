import pytest
from shadow_sandbox.remediation.policy_engine import PolicyEngine
from contracts.reason_codes import ReasonCode

def test_policy_registered_intent():
    pe = PolicyEngine()
    intent = {"intent_type": "postgres.setting.update"}
    target = {"kind": "container", "canonical_name": "shadow-postgres-db"}
    ok, code, msg = pe.evaluate_intent(intent, target)
    assert ok is True

def test_policy_unknown_intent():
    pe = PolicyEngine()
    intent = {"intent_type": "unknown.custom.tool"}
    target = {"kind": "container", "canonical_name": "shadow-api"}
    ok, code, msg = pe.evaluate_intent(intent, target)
    assert ok is False
    assert code == ReasonCode.BLOCKED_UNKNOWN_CAPABILITY

def test_policy_high_risk_intent():
    pe = PolicyEngine()
    intent = {"intent_type": "postgres.wal.archive_cleanup", "requires_human_approval": True}
    target = {"kind": "container", "canonical_name": "shadow-postgres-db"}
    ok, code, msg = pe.evaluate_intent(intent, target)
    assert ok is False
    assert code == ReasonCode.REQUIRES_HUMAN_APPROVAL
