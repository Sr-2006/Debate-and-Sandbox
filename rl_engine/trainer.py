import time
import hashlib
from typing import Dict, Any, Tuple, Optional
from rl_engine.config import RL_ALPHA
from rl_engine.policy import SafeDisjointLinUCB
from rl_engine.episode_store import EpisodeStore
from rl_engine.model_store import ModelStore


def train_linucb_policy(
    db_path: Optional[str] = None,
    alpha: float = RL_ALPHA,
    output_dir: Optional[str] = None
) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Trains a SafeDisjointLinUCB policy on eligible real episodes from database.
    """
    epstore = EpisodeStore()
    episodes = epstore.get_eligible_episodes(db_path)
    total_count = len(episodes)

    if total_count < 10:
        return None, {
            "status": "INSUFFICIENT_EPISODES",
            "eligible_count": total_count,
            "required": 10,
            "message": "Fewer than 10 eligible real episodes; training skipped"
        }

    # 70% train split
    train_end = int(total_count * 0.70)
    train_eps = episodes[:train_end]
    val_eps = episodes[train_end:]

    policy = SafeDisjointLinUCB(alpha=alpha)
    for ep in train_eps:
        policy.update(
            action=ep["action"],
            feature_vector=ep["feature_vector"],
            reward=ep["reward"],
            sample_weight=ep.get("sample_weight", 1.0)
        )

    # Version tag
    ts = time.strftime("%Y%m%d-%H%M%S")
    short_hash = hashlib.sha256(f"{ts}_{total_count}".encode("utf-8")).hexdigest()[:8]
    model_version = f"rl-mvp-{ts}-{short_hash}"

    eval_summary = {
        "training_episodes": len(train_eps),
        "validation_episodes": len(val_eps),
        "total_eligible_episodes": total_count
    }

    mstore = ModelStore(output_dir) if output_dir else ModelStore()
    filepath = mstore.save_model(
        policy=policy,
        model_version=model_version,
        training_episode_count=len(train_eps),
        training_cutoff=episodes[-1]["created_at"] if episodes else "",
        evaluation_summary=eval_summary
    )

    return model_version, eval_summary
