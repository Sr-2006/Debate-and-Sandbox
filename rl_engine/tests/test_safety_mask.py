import pytest
from rl_engine.safety_mask import get_allowed_actions


def test_safety_mask_phase3_failed_allows_only_abstain():
    actions, reasons = get_allowed_actions("PHASE3_FAILED", 0.0, False, "MUTATE_REVERSIBLE", False, True, True, ["log1"])
    assert actions == ["ABSTAIN"]
    assert "RL_PHASE3_FAILED" in reasons


def test_safety_mask_unmapped_capability_allows_only_abstain():
    actions, reasons = get_allowed_actions("SUCCESS", 0.9, False, "MUTATE_REVERSIBLE", False, False, True, ["log1"])
    assert actions == ["ABSTAIN"]
    assert "RL_UNMAPPED_CAPABILITY" in reasons


def test_safety_mask_low_confidence_permits_only_observe_or_abstain():
    actions, reasons = get_allowed_actions("SUCCESS", 0.40, False, "MUTATE_REVERSIBLE", False, True, True, ["log1"])
    assert "ACCEPT_PROPOSAL" not in actions
    assert "REQUIRE_HUMAN_REVIEW" not in actions
    assert set(actions) == {"OBSERVE_FIRST", "ABSTAIN"}
    assert "RL_LOW_CONFIDENCE" in reasons


def test_safety_mask_safety_violation_never_allows_accept_proposal():
    actions, reasons = get_allowed_actions("SUCCESS", 0.95, True, "MUTATE_REVERSIBLE", False, True, True, ["log1"])
    assert "ACCEPT_PROPOSAL" not in actions
    assert set(actions) == {"REQUIRE_HUMAN_REVIEW", "ABSTAIN"}
    assert "RL_SAFETY_VIOLATION" in reasons


def test_safety_mask_high_risk_requires_human_approval_or_abstain():
    actions, reasons = get_allowed_actions("SUCCESS", 0.95, False, "MUTATE_HIGH_RISK", True, True, True, ["log1"])
    assert "ACCEPT_PROPOSAL" not in actions
    assert set(actions) == {"REQUIRE_HUMAN_REVIEW", "ABSTAIN"}


def test_safety_mask_supported_reversible_mutation_allows_all_four():
    actions, reasons = get_allowed_actions("SUCCESS", 0.85, False, "MUTATE_REVERSIBLE", False, True, True, ["log1"])
    assert len(actions) == 4
    assert set(actions) == {"ACCEPT_PROPOSAL", "OBSERVE_FIRST", "REQUIRE_HUMAN_REVIEW", "ABSTAIN"}
