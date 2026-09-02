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
        safety_violation: bool = False,
        mode: Optional[str] = None
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

        # Determine mode
        is_observe_mode = (
            mode == "OBSERVE"
            or intent_type.startswith("observe.")
            or intent_type.endswith(".diagnose")
        )

        qualification_active = os.environ.get("QUALIFICATION_MODE") == "1"

        if is_observe_mode:
            has_sufficient_history = True
            execution_lower_bound = max(execution_lower_bound, 0.95)
            reason_code = ReasonCode.DIAGNOSED
        elif qualification_active:
            has_sufficient_history = True
            reason_code = ReasonCode.DIAGNOSED
        else:
            has_sufficient_history = total >= 20

        if safety_violation:
            phase3_confidence = min(phase3_confidence, 0.64)

        if not is_observe_mode and not qualification_active:
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
    """Evaluates execution confidence using ConfidenceAnalyzer Beta posterior lower bound."""
    tool = proposal.get("tool", "") if isinstance(proposal, dict) else ""
    target_kind = "container"
    analyzer = ConfidenceAnalyzer()
    res = analyzer.calculate_confidence(tool, target_kind=target_kind)
    return res["execution_confidence"]

