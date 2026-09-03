import pytest
from rl_engine.trainer import train_linucb_policy


def test_trainer_insufficient_episodes_fallback(tmp_path):
    db_file = tmp_path / "sandbox_empty.db"
    model_dir = tmp_path / "models"

    mver, summary = train_linucb_policy(db_path=str(db_file), output_dir=str(model_dir))
    assert mver is None
    assert summary["status"] == "INSUFFICIENT_EPISODES"
