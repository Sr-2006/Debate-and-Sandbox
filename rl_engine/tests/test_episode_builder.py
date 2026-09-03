import pytest
from rl_engine.advisor import RLAdvisor
from rl_engine.episode_builder import build_learning_episode


def test_build_learning_episode_simulation_outcome():
    advisor = RLAdvisor()
    envelope = {
        "incident_id": "case_01",
        "phase3_confidence": {"score": 0.88},
        "evidence_refs": ["log_1"],
        "intents": [
            {
                "intent_type": "container.restart",
                "mode": "MUTATE_REVERSIBLE",
                "risk_class": "LOW",
                "target_ref": {"kind": "container", "canonical_name": "app"}
            }
        ]
    }
    advisory = advisor.generate_advisory(envelope)
    p4_result = {
        "status": "SIMULATION_VERIFIED",
        "simulated": True,
        "attestation": {"attested": True},
        "execution": {"result": {"success": True}},
        "verification": {"passed": True},
        "rollback": {"attempted": False}
    }

    ep = build_learning_episode(advisory, envelope, p4_result, "run_100")
    assert ep.schema_version == "1.0"
    assert ep.episode_id.startswith("ep_")
    assert ep.phase4.status == "SIMULATION_VERIFIED"
    assert ep.learning.eligible is False
    assert ep.learning.reward is None
    assert ep.learning.behavior_action == "ACCEPT_PROPOSAL"
