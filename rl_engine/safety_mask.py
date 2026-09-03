from typing import Dict, Any, List, Tuple
from rl_engine.config import ROUTING_ACTIONS


def get_allowed_actions(
    p3_status: str,
    confidence: float,
    safety_violation: bool,
    mode: str,
    human_approval: bool,
    capability_mapped: bool,
    mvp_supported: bool,
    evidence_refs: List[str],
    target_resolved: bool = True
) -> Tuple[List[str], List[str]]:
    """
    Applies the monotonic safety mask to return allowed actions and associated reason codes.
    """
    reasons: List[str] = []

    if p3_status == "PHASE3_FAILED":
        reasons.append("RL_PHASE3_FAILED")
        return ["ABSTAIN"], reasons

    if not target_resolved:
        reasons.append("RL_TARGET_UNRESOLVED")
        return ["ABSTAIN"], reasons

    if not capability_mapped:
        reasons.append("RL_UNMAPPED_CAPABILITY")
        return ["ABSTAIN"], reasons

    if not mvp_supported:
        reasons.append("RL_UNSUPPORTED_IN_MVP")
        return ["ABSTAIN"], reasons

    if not evidence_refs:
        reasons.append("RL_MISSING_EVIDENCE")
        return ["ABSTAIN", "REQUIRE_HUMAN_REVIEW"], reasons

    if safety_violation:
        reasons.append("RL_SAFETY_VIOLATION")
        return ["REQUIRE_HUMAN_REVIEW", "ABSTAIN"], reasons

    if mode == "MUTATE_HIGH_RISK" or human_approval:
        reasons.append("RL_HIGH_RISK_HUMAN_APPROVAL")
        return ["REQUIRE_HUMAN_REVIEW", "ABSTAIN"], reasons

    if confidence is None or confidence < 0.50:
        reasons.append("RL_LOW_CONFIDENCE")
        return ["OBSERVE_FIRST", "ABSTAIN"], reasons

    if mode == "OBSERVE":
        return ["ACCEPT_PROPOSAL", "ABSTAIN"], reasons

    if mode == "MUTATE_REVERSIBLE":
        return list(ROUTING_ACTIONS), reasons

    reasons.append("RL_EMPTY_ACTION_MASK")
    return ["ABSTAIN"], reasons
