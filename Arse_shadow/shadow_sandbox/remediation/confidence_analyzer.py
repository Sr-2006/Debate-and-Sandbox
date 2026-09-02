import math
import os
import json
from datetime import datetime, timezone
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
        phase3_confidence: Optional[float] = None,
        safety_violation: bool = False,
        mode: Optional[str] = None,
        qualification_context: Optional[Dict[str, Any]] = None,
        target_name: Optional[str] = None
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

        # Determine mode & qualification context
        is_observe_mode = (
            mode == "OBSERVE"
            or intent_type.startswith("observe.")
            or intent_type.endswith(".diagnose")
        )

        qualification_active = False
        qualification_invalid = False
        qual_msg = ""

        if qualification_context and isinstance(qualification_context, dict):
            q_run = qualification_context.get("qualification_run", False)
            run_id = qualification_context.get("run_id")
            req_by = qualification_context.get("requested_by")
            auth_caps = qualification_context.get("authorized_capabilities", [])
            allowed_targets = qualification_context.get("allowed_targets", [])
            expires_at = qualification_context.get("expires_at", "")
            prod_eligible = qualification_context.get("production_eligible", False)

            if q_run:
                # Mandatory fields validation
                if not run_id or not str(run_id).strip():
                    qualification_invalid = True
                    qual_msg = "Qualification context missing required 'run_id'"
                elif not req_by or not str(req_by).strip():
                    qualification_invalid = True
                    qual_msg = "Qualification context missing required 'requested_by'"
                elif prod_eligible:
                    qualification_invalid = True
                    qual_msg = "Qualification context cannot have production_eligible=True"
                elif not auth_caps or intent_type not in auth_caps:
                    qualification_invalid = True
                    qual_msg = f"Capability '{intent_type}' not in authorized_capabilities {auth_caps}"
                elif not allowed_targets or (target_name and target_name not in allowed_targets):
                    qualification_invalid = True
                    qual_msg = f"Target '{target_name}' not in allowed_targets {allowed_targets}"
                else:
                    # Expiry validation
                    if not expires_at:
                        qualification_invalid = True
                        qual_msg = "Qualification context missing mandatory ISO 'expires_at' timestamp"
                    else:
                        try:
                            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                            now_dt = datetime.now(timezone.utc)
                            if now_dt > exp_dt:
                                qualification_invalid = True
                                qual_msg = f"Qualification context expired at {expires_at}"
                            else:
                                qualification_active = True
                        except Exception as e:
                            qualification_invalid = True
                            qual_msg = f"Invalid ISO expires_at format '{expires_at}': {str(e)}"

        if qualification_invalid:
            return {
                "has_sufficient_history": False,
                "execution_confidence": round(execution_lower_bound, 4),
                "sample_size": total,
                "reason_code": ReasonCode.BLOCKED_INVALID_QUALIFICATION.value,
                "confidence_required": True,
                "authorization_basis": "INVALID_QUALIFICATION",
                "detail": qual_msg
            }




        confidence_required = not is_observe_mode

        if is_observe_mode:
            has_sufficient_history = True
            authorization_basis = "READ_ONLY_POLICY"
            reason_code = ReasonCode.DIAGNOSED
        elif qualification_active:
            has_sufficient_history = True
            authorization_basis = "QUALIFICATION_RUN"
            reason_code = ReasonCode.DIAGNOSED
        else:
            authorization_basis = "EMPIRICAL_BETA_POSTERIOR"
            has_sufficient_history = total >= 20

        if safety_violation and phase3_confidence is not None:
            phase3_confidence = min(phase3_confidence, 0.64)

        if confidence_required and not qualification_active:
            reason_code = ReasonCode.DIAGNOSED
            if not has_sufficient_history:
                reason_code = ReasonCode.INSUFFICIENT_HISTORY
            elif execution_lower_bound < 0.70:
                reason_code = ReasonCode.BLOCKED_LOW_CONFIDENCE

        return {
            "diagnosis_confidence": round(phase3_confidence, 4) if phase3_confidence is not None else None,
            "mapping_confidence": mapping_conf,

            "execution_confidence": round(execution_lower_bound, 4),
            "confidence_required": confidence_required,
            "authorization_basis": authorization_basis,
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

