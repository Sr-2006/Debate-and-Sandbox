from typing import Dict, Any, Tuple, Optional


def evaluate_outcome_reward(phase4_result: Dict[str, Any]) -> Tuple[bool, str, Optional[float], float]:
    """
    Evaluates real sandbox execution outcome to determine reward and training eligibility.
    Returns: (eligible, eligibility_reason, reward, sample_weight)
    """
    status = phase4_result.get("status", "NOT_RUN")
    simulated = bool(phase4_result.get("simulated", False))
    
    if simulated or status == "SIMULATION_VERIFIED":
        return False, "SIMULATION_ONLY", None, 0.0

    if status == "SANDBOX_VERIFIED":
        return True, "VERIFIED_SUCCESS", 1.0, 1.0

    if status == "SANDBOX_FAILED_ROLLED_BACK":
        return True, "FAILED_ROLLED_BACK", -0.5, 1.0

    if status == "SANDBOX_FAILED_ROLLBACK_FAILED":
        return True, "FAILED_ROLLBACK_FAILED", -1.0, 1.0

    if status == "PRECONDITION_FAILED":
        attestation = phase4_result.get("attestation", {})
        attested = bool(attestation.get("attested", False))
        if attested:
            return True, "PRECONDITION_FAILED_ATTESTED", -0.2, 1.0
        return False, "PRECONDITION_FAILED_UNATTESTED", None, 0.0

    if status in ["ATTESTATION_FAILED", "VALIDATION_FAILED", "NO_SUPPORTED_ACTION", "UNSUPPORTED_IN_MVP", "HUMAN_REVIEW_REQUIRED", "READ_ONLY_OBSERVED", "NOT_RUN", "PHASE3_FAILED"]:
        return False, f"STATUS_{status}", None, 0.0

    return False, f"UNHANDLED_{status}", None, 0.0
