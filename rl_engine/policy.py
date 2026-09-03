import numpy as np
import math
from typing import Dict, List, Any, Optional, Tuple
from rl_engine.config import ROUTING_ACTIONS, RL_ALPHA


class SafeDisjointLinUCB:
    """Disjoint LinUCB Policy for safe routing action recommendation."""

    def __init__(self, feature_dim: int = 44, alpha: float = RL_ALPHA):
        self.feature_dim = feature_dim
        self.alpha = alpha
        self.actions = ROUTING_ACTIONS
        self.A: Dict[str, np.ndarray] = {
            act: np.eye(feature_dim, dtype=np.float64) for act in self.actions
        }
        self.b: Dict[str, np.ndarray] = {
            act: np.zeros(feature_dim, dtype=np.float64) for act in self.actions
        }

    def predict(self, feature_vector: List[float], allowed_actions: List[str]) -> Tuple[str, Dict[str, Optional[float]], float]:
        """
        Predicts scores for allowed actions and returns: (recommended_action, action_scores, uncertainty).
        """
        x = np.array(feature_vector, dtype=np.float64)
        if len(x) != self.feature_dim:
            # Re-dimension if mismatch
            x = np.resize(x, (self.feature_dim,))

        scores: Dict[str, Optional[float]] = {act: None for act in self.actions}
        best_action = None
        best_score = -float("inf")
        selected_uncertainty = 0.0

        for act in self.actions:
            if act not in allowed_actions:
                scores[act] = None
                continue

            A_inv_x = np.linalg.solve(self.A[act], x)
            theta = np.linalg.solve(self.A[act], self.b[act])
            
            expected_r = float(np.dot(theta, x))
            var = float(np.dot(x, A_inv_x))
            uncertainty = self.alpha * math.sqrt(max(0.0, var))
            score = expected_r + uncertainty

            scores[act] = round(score, 4)
            if score > best_score:
                best_score = score
                best_action = act
                selected_uncertainty = round(uncertainty, 4)

        if best_action is None:
            best_action = "ABSTAIN"

        return best_action, scores, selected_uncertainty

    def update(self, action: str, feature_vector: List[float], reward: float, sample_weight: float = 1.0):
        """Updates LinUCB parameters for chosen action with feature vector and reward."""
        if action not in self.actions:
            return
        x = np.array(feature_vector, dtype=np.float64)
        if len(x) != self.feature_dim:
            x = np.resize(x, (self.feature_dim,))

        self.A[action] += sample_weight * np.outer(x, x)
        self.b[action] += sample_weight * reward * x

    def to_dict(self) -> Dict[str, Any]:

        """Serializes matrices and configuration to JSON-compatible dictionary."""
        return {
            "policy_name": "safe_disjoint_linucb",
            "feature_dim": self.feature_dim,
            "alpha": self.alpha,
            "actions": {
                act: {
                    "A": self.A[act].tolist(),
                    "b": self.b[act].tolist()
                }
                for act in self.actions
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SafeDisjointLinUCB':
        feature_dim = data.get("feature_dim", 44)
        alpha = data.get("alpha", RL_ALPHA)
        instance = cls(feature_dim=feature_dim, alpha=alpha)

        actions_data = data.get("actions", {})
        for act, mat in actions_data.items():
            if act in instance.actions:
                instance.A[act] = np.array(mat["A"], dtype=np.float64)
                instance.b[act] = np.array(mat["b"], dtype=np.float64)
        return instance
