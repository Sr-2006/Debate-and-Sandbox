import pytest
from rl_engine.evaluator import evaluate_policy


def test_evaluator_cold_start():
    res = evaluate_policy("cold-start")
    assert res["status"] in ["COLD_START", "EVALUATED"]
    assert res["model_version"] == "cold-start"
