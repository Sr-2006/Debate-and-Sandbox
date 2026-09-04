"""RL Feedback builder for Post-Phase 4 reward calculation and feedback event generation."""

from typing import Any, Dict, Optional

from rl_engine.config import RL_POLICY_VERSION, RL_REWARD_VERSION


def calculate_remediation_reward(
    outcome: str,
    verification_status: str,
    rollback_status: str,
    duration_ms: float = 0.0
) -> float:
    """
    Computes a deterministic scalar reward in [-1.0, 1.0] for the RL episode.
      +1.0 : SANDBOX_VERIFIED and verification PASSED
      -0.5 : Failed verification requiring rollback
      -1.0 : Catastrophic failure or safety violation
      +0.0 : Observe / non-mutating pass
    """
    if outcome == "SANDBOX_VERIFIED" and verification_status == "PASSED":
        return 1.0
    elif outcome == "REVERSIBLE_EXECUTED" and verification_status == "PASSED":
        return 0.8
    elif verification_status == "FAILED" or rollback_status in ["COMPLETED", "EXECUTED"]:
        return -0.5
    elif outcome in ["EXECUTION_FAILED", "REJECTED_SAFETY"]:
        return -1.0
    elif outcome == "NOT_ATTEMPTED":
        return 0.0
    return 0.1


def build_rl_feedback_payload(
    advisory: Optional[Dict[str, Any]],
    phase4_result: Optional[Dict[str, Any]],
    final_outcome: str,
    feature_hash: Optional[str] = None
) -> Dict[str, Any]:
    """
    Constructs the structured payload for autosre.rl.feedback.v1.
    """
    adv_safe = advisory or {}
    p4_safe = phase4_result or {}

    exec_sec = p4_safe.get("execution", {})
    ver_sec = p4_safe.get("verification", {})
    rb_sec = p4_safe.get("rollback", {})

    verification_status = ver_sec.get("status", "NOT_RUN")
    rollback_status = rb_sec.get("status", "NOT_RUN")

    recommended_action = adv_safe.get("policy_action") or adv_safe.get("advisory_decision") or "OBSERVE_FIRST"
    executed_action = exec_sec.get("capability") or adv_safe.get("execution_capability") or "NONE"

    reward = calculate_remediation_reward(
        outcome=final_outcome,
        verification_status=verification_status,
        rollback_status=rollback_status
    )

    feat_hash = feature_hash or adv_safe.get("feature_hash") or "unknown_feature_hash"
    policy_ver = adv_safe.get("policy", {}).get("policy_version") or RL_POLICY_VERSION

    return {
        "recommended_action": recommended_action,
        "executed_action": executed_action,
        "execution_capability": executed_action,
        "outcome": final_outcome,
        "verification_result": verification_status,
        "rollback_result": rollback_status,
        "reward": reward,
        "reward_schema_version": RL_REWARD_VERSION,
        "feature_hash": feat_hash,
        "policy_version": policy_ver,
        "details": {
            "attestation_status": p4_safe.get("attestation", {}).get("status", "NOT_RUN"),
            "execution_status": exec_sec.get("status", "NOT_RUN"),
            "verification_status": verification_status,
            "rollback_status": rollback_status
        }
    }
