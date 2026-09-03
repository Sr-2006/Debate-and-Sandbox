import pytest
from rl_engine.reward import evaluate_outcome_reward


def test_reward_sandbox_verified_returns_plus_one():
    res = {"status": "SANDBOX_VERIFIED", "simulated": False}
    eligible, reason, reward, weight = evaluate_outcome_reward(res)
    assert eligible is True
    assert reward == 1.0
    assert weight == 1.0


def test_reward_simulation_verified_returns_ineligible_and_null():
    res = {"status": "SIMULATION_VERIFIED", "simulated": True}
    eligible, reason, reward, weight = evaluate_outcome_reward(res)
    assert eligible is False
    assert reason == "SIMULATION_ONLY"
    assert reward is None
    assert weight == 0.0


def test_reward_sandbox_failed_rolled_back():
    res = {"status": "SANDBOX_FAILED_ROLLED_BACK", "simulated": False}
    eligible, reason, reward, weight = evaluate_outcome_reward(res)
    assert eligible is True
    assert reward == -0.5
