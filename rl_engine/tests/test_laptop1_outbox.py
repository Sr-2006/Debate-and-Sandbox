import pytest
import os
from rl_engine.config import RL_LAPTOP1_TRANSPORT
from rl_engine.episode_store import EpisodeStore
from rl_engine.contracts import LearningEpisodeData, EpisodeContext, ProposalRef, Phase4Outcome, EpisodeLearning


def test_laptop1_outbox_disabled_by_default():
    assert RL_LAPTOP1_TRANSPORT == "disabled"


def test_outbox_file_written(tmp_path):
    outbox_dir = tmp_path / "outbox"
    store = EpisodeStore(outbox_dir=str(outbox_dir))

    ep = LearningEpisodeData(
        schema_version="1.0",
        episode_id="ep_outbox_test",
        incident_id="case_outbox",
        run_id="run_outbox",
        payload_hash="hash_outbox",
        created_at="2026-09-03T10:00:00Z",
        context=EpisodeContext("features-v1", {}, [0.0]*44, "hash_outbox"),
        proposal=ProposalRef("container.restart", "container", "MUTATE_REVERSIBLE", "LOW"),
        advisory={},
        phase4=Phase4Outcome("SIMULATION_VERIFIED", True, True, True, True, False, False),
        learning=EpisodeLearning(False, "SIMULATION_ONLY", None, 0.0, "ACCEPT_PROPOSAL")
    )

    store.save_episode(ep, db_path=str(tmp_path / "test.db"))
    files = os.listdir(outbox_dir)
    assert "ep_outbox_test.learning_episode.json" in files
