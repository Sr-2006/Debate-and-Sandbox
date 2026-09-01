import math
import os
import json
from typing import Dict, Any, Tuple, Optional
from scipy.stats import beta  # type: ignore
from shadow_sandbox.persistence import SandboxPersistence
from contracts.reason_codes import ReasonCode

class ConfidenceAnalyzer:
    """
    Evaluates multi-score confidence:
    1. diagnosis_confidence: Phase 3 LLM root-cause probability.
    2. mapping_confidence: Complete typed catalog intent resolution (binary 1.0 or 0.0).
    3. execution_confidence: Beta posterior 5th percentile lower bound from verified sandbox outcomes.
    """

    def __init__(self, persistence: Optional[SandboxPersistence] = None):
        self.persistence = persistence or SandboxPersistence()

    def calculate_confidence(
        self,
        intent_type: str,
        target_kind: str = "container",
        phase3_confidence: float = 0.85,
        safety_violation: bool = False
    ) -> Dict[str, Any]:
        mapping_conf = 1.0 if intent_type else 0.0
        hist = self.persistence.get_capability_history(intent_type, target_kind)
        total = hist["total"]
        successes = hist["successes"]
        failures = hist["failures"]

        alpha_param = 1 + successes
        beta_param = 1 + failures

        try:
            execution_lower_bound = float(beta.ppf(0.05, alpha_param, beta_param))
        except Exception:
            mean = alpha_param / (alpha_param + beta_param)
            var = (alpha_param * beta_param) / ((alpha_param + beta_param) ** 2 * (alpha_param + beta_param + 1))
            execution_lower_bound = max(0.0, mean - 1.645 * math.sqrt(var))

        has_sufficient_history = total >= 20

        if safety_violation:
            phase3_confidence = min(phase3_confidence, 0.64)

        reason_code = ReasonCode.DIAGNOSED
        if not has_sufficient_history:
            reason_code = ReasonCode.INSUFFICIENT_HISTORY
        elif execution_lower_bound < 0.70:
            reason_code = ReasonCode.BLOCKED_LOW_CONFIDENCE

        return {
            "diagnosis_confidence": round(phase3_confidence, 4),
            "mapping_confidence": mapping_conf,
            "execution_confidence": round(execution_lower_bound, 4),
            "sample_size": total,
            "successes": successes,
            "failures": failures,
            "has_sufficient_history": has_sufficient_history,
            "reason_code": reason_code.value
        }


def calculate_confidence(proposal: Dict[str, Any], history_path: Optional[str] = None) -> float:
    """Legacy helper function returning float score for legacy test compatibility."""
    target = proposal.get("target", "") if isinstance(proposal, dict) else ""
    tool = proposal.get("tool", "") if isinstance(proposal, dict) else ""

    target_penalty = 0.15 if ("postgres" in target or "redis" in target) else 0.0
    tool_penalty = 0.10 if any(kw in tool for kw in ["query", "restart", "setting", "postgres"]) else 0.0

    mult = 0.85
    if history_path and os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data and isinstance(data, list):
                    mult = 1.0
        except Exception:
            pass

    score = (1.0 - target_penalty - tool_penalty) * mult
    return round(score, 2)
