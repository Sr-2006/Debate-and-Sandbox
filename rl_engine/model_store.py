import os
import json
import hashlib
import time
from typing import Dict, Any, Optional, Tuple

from rl_engine.config import RL_MODEL_DIR, RL_POLICY_NAME, RL_POLICY_VERSION, RL_FEATURE_VERSION, RL_REWARD_VERSION
from rl_engine.policy import SafeDisjointLinUCB


def compute_artifact_checksum(data: Dict[str, Any]) -> str:
    serialized = json.dumps(data, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class ModelStore:
    """Manages versioned, checksum-validated JSON serialization of RL policy models."""

    def __init__(self, model_dir: str = RL_MODEL_DIR):
        self.model_dir = model_dir
        os.makedirs(self.model_dir, exist_ok=True)

    def save_model(
        self,
        policy: SafeDisjointLinUCB,
        model_version: str,
        training_episode_count: int = 0,
        training_cutoff: str = "",
        evaluation_summary: Optional[Dict[str, Any]] = None
    ) -> str:
        """Atomically saves policy artifact as JSON and returns artifact file path."""
        policy_dict = policy.to_dict()
        checksum = compute_artifact_checksum(policy_dict)

        artifact = {
            "policy_name": RL_POLICY_NAME,
            "policy_version": RL_POLICY_VERSION,
            "model_version": model_version,
            "feature_schema_version": RL_FEATURE_VERSION,
            "feature_dimension": policy.feature_dim,
            "reward_version": RL_REWARD_VERSION,
            "alpha": policy.alpha,
            "training_episode_count": training_episode_count,
            "training_cutoff": training_cutoff or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "artifact_checksum": checksum,
            "evaluation_summary": evaluation_summary or {},
            "policy_data": policy_dict
        }

        filename = f"{model_version}.json"
        filepath = os.path.join(self.model_dir, filename)
        tmppath = filepath + ".tmp"

        with open(tmppath, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2)

        os.replace(tmppath, filepath)
        return filepath

    def load_model(self, model_version: str = "promoted") -> Tuple[Optional[SafeDisjointLinUCB], Dict[str, Any]]:
        """Loads and verifies a policy artifact by version name or file path."""
        from rl_engine.config import RL_FEATURE_DIMENSION
        if model_version == "cold-start":
            return SafeDisjointLinUCB(feature_dim=RL_FEATURE_DIMENSION), {
                "model_version": "cold-start",
                "cold_start": True,
                "feature_schema_version": RL_FEATURE_VERSION,
                "feature_dimension": RL_FEATURE_DIMENSION
            }

        if os.path.exists(model_version):
            filepath = model_version
        else:
            filepath = os.path.join(self.model_dir, f"{model_version}.json")

        if not os.path.exists(filepath):
            return SafeDisjointLinUCB(feature_dim=RL_FEATURE_DIMENSION), {
                "model_version": "cold-start",
                "cold_start": True,
                "feature_schema_version": RL_FEATURE_VERSION,
                "feature_dimension": RL_FEATURE_DIMENSION
            }

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                artifact = json.load(f)

            policy_data = artifact.get("policy_data", {})
            checksum_stored = artifact.get("artifact_checksum")
            checksum_actual = compute_artifact_checksum(policy_data)

            if checksum_stored and checksum_stored != checksum_actual:
                # Checksum mismatch; fall back to cold-start
                return SafeDisjointLinUCB(feature_dim=RL_FEATURE_DIMENSION), {
                    "model_version": "cold-start",
                    "cold_start": True,
                    "error": "checksum_mismatch",
                    "feature_schema_version": RL_FEATURE_VERSION,
                    "feature_dimension": RL_FEATURE_DIMENSION
                }

            policy = SafeDisjointLinUCB.from_dict(policy_data)
            return policy, artifact
        except Exception:
            return SafeDisjointLinUCB(feature_dim=RL_FEATURE_DIMENSION), {
                "model_version": "cold-start",
                "cold_start": True,
                "error": "load_failed",
                "feature_schema_version": RL_FEATURE_VERSION,
                "feature_dimension": RL_FEATURE_DIMENSION
            }
