import pytest
import os
import tempfile
from rl_engine.advisor import RLAdvisor
from rl_engine.episode_builder import build_learning_episode
from rl_engine.episode_store import EpisodeStore


def test_episode_store_save_advisory_and_episode(tmp_path):
    db_file = tmp_path / "sandbox_test.db"
    outbox_dir = tmp_path / "outbox"

    store = EpisodeStore(outbox_dir=str(outbox_dir))
    advisor = RLAdvisor()
    envelope = {
        "incident_id": "case_test_store",
        "phase3_confidence": {"score": 0.90},
        "evidence_refs": ["log_1"],
        "intents": [
            {
                "intent_type": "container.restart",
                "mode": "MUTATE_REVERSIBLE",
                "risk_class": "LOW"
            }
        ]
    }
    advisory = advisor.generate_advisory(envelope, run_id="run_test_1")
    p4_res = {"status": "SIMULATION_VERIFIED", "simulated": True}
    episode = build_learning_episode(advisory, envelope, p4_res, run_id="run_test_1")

    saved_adv = store.save_advisory(advisory, db_path=str(db_file))
    saved_ep = store.save_episode(episode, db_path=str(db_file))

    assert saved_adv is True
    assert saved_ep is True

    # Outbox file created
    outbox_files = os.listdir(outbox_dir)
    assert any(f.endswith(".learning_episode.json") for f in outbox_files)
