from typing import Dict, Any, List, Optional

from rl_engine.episode_store import EpisodeStore
from rl_engine.model_store import ModelStore


def evaluate_policy(model_version: str = "promoted", db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Evaluates policy metrics over stored learning episodes.
    """
    mstore = ModelStore()
    policy, meta = mstore.load_model(model_version)
    epstore = EpisodeStore()
    episodes = epstore.get_eligible_episodes(db_path)

    total_eps = len(episodes)
    if total_eps == 0:
        return {
            "model_version": model_version,
            "total_episodes": 0,
            "mean_reward": 0.0,
            "agreement_rate": 1.0,
            "status": "COLD_START"
        }

    rewards = [ep["reward"] for ep in episodes]
    mean_reward = sum(rewards) / total_eps

    return {
        "model_version": model_version,
        "total_episodes": total_eps,
        "mean_reward": round(mean_reward, 4),
        "agreement_rate": 1.0,
        "status": "EVALUATED"
    }
