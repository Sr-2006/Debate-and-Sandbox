import pytest
from rl_engine.contracts import RLAdvisoryData, PolicyRef, ProposalRef, LearningEpisodeData, EpisodeContext, Phase4Outcome, EpisodeLearning


def test_rl_advisory_contract_serialization():
    adv = RLAdvisoryData(
        schema_version="1.0",
        advisory_id="adv_1234567890ab",
        incident_id="case_01",
        run_id="run_100",
        created_at="2026-09-03T10:00:00Z",
        policy=PolicyRef("safe_disjoint_linucb", "rl-mvp-1", "cold-start", "SHADOW"),
        proposal=ProposalRef("container.restart", "container", "MUTATE_REVERSIBLE", "LOW"),
        recommendation="OBSERVE_FIRST",
        action_scores={"ACCEPT_PROPOSAL": 0.1, "OBSERVE_FIRST": 0.5, "REQUIRE_HUMAN_REVIEW": 0.1, "ABSTAIN": 0.1},
        uncertainty=0.5,
        sample_size=0,
        cold_start=True,
        influence_allowed=False,
        reason_codes=["INSUFFICIENT_REAL_OUTCOMES"],
        feature_schema_version="features-v1",
        feature_hash="hash123",
        latency_ms=1.2,
        estimated_success_probability=0.5
    )
    d = adv.to_dict()
    assert d["schema_version"] == "1.0"
    assert d["recommendation"] == "OBSERVE_FIRST"
    assert d["influence_allowed"] is False
    assert d["cold_start"] is True


def test_learning_episode_contract_simulation_reward_null():
    ep = LearningEpisodeData(
        schema_version="1.0",
        episode_id="ep_1234567890ab",
        incident_id="case_01",
        run_id="run_100",
        payload_hash="hash123",
        created_at="2026-09-03T10:00:00Z",
        context=EpisodeContext("features-v1", {"p3": 0.8}, [0.8]*44, "hash123"),
        proposal=ProposalRef("container.restart", "container", "MUTATE_REVERSIBLE", "LOW"),
        advisory={},
        phase4=Phase4Outcome("SIMULATION_VERIFIED", True, True, True, True, False, False),
        learning=EpisodeLearning(False, "SIMULATION_ONLY", None, 0.0, "ACCEPT_PROPOSAL")
    )
    d = ep.to_dict()
    assert d["learning"]["eligible"] is False
    assert d["learning"]["reward"] is None
    assert d["learning"]["sample_weight"] == 0.0
