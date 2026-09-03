import pytest
from rl_engine.policy import SafeDisjointLinUCB


def test_linucb_policy_predict_and_update():
    policy = SafeDisjointLinUCB(feature_dim=44)
    x = [0.1] * 44

    allowed = ["ACCEPT_PROPOSAL", "OBSERVE_FIRST", "REQUIRE_HUMAN_REVIEW", "ABSTAIN"]
    best_act, scores, unc = policy.predict(x, allowed)
    assert best_act in allowed
    assert len(scores) == 4

    # Update ACCEPT_PROPOSAL with positive reward
    for _ in range(5):
        policy.update("ACCEPT_PROPOSAL", x, reward=1.0)

    best_act2, scores2, unc2 = policy.predict(x, allowed)
    assert best_act2 == "ACCEPT_PROPOSAL"
    assert scores2["ACCEPT_PROPOSAL"] > scores2["ABSTAIN"]


def test_linucb_policy_serialization():
    policy = SafeDisjointLinUCB(feature_dim=44)
    d = policy.to_dict()
    assert d["policy_name"] == "safe_disjoint_linucb"
    assert d["feature_dim"] == 44

    restored = SafeDisjointLinUCB.from_dict(d)
    assert restored.feature_dim == 44
    assert len(restored.actions) == 4
