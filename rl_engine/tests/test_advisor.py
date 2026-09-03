import pytest
from rl_engine.advisor import RLAdvisor


def test_advisor_shadow_mode_never_allows_influence():
    advisor = RLAdvisor(operating_mode="SHADOW")
    envelope = {
        "incident_id": "case_01",
        "phase3_confidence": {"score": 0.88},
        "evidence_refs": ["log_1"],
        "intents": [
            {
                "intent_type": "container.restart",
                "mode": "MUTATE_REVERSIBLE",
                "risk_class": "LOW",
                "requires_human_approval": False
            }
        ]
    }

    advisory = advisor.generate_advisory(envelope)
    assert advisory.policy.operating_mode == "SHADOW"
    assert advisory.influence_allowed is False
    assert advisory.cold_start is True


def test_advisor_fail_open_on_corrupt_input():
    advisor = RLAdvisor(operating_mode="SHADOW")
    advisory = advisor.generate_advisory(None)  # Corrupt input
    assert advisory.recommendation == "ABSTAIN"
    assert advisory.influence_allowed is False
    assert "RL_ADVISOR_UNAVAILABLE" in advisory.reason_codes
