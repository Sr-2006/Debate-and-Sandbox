import math
import hashlib
import json
from typing import Dict, Any, Tuple, List, Optional
from rl_engine.config import CATEGORICAL_VOCAB, RL_FEATURE_VERSION
from contracts.validation import is_mvp_supported, get_capabilities


def _one_hot(value: str, vocab: List[str]) -> List[float]:
    normalized_val = value if value in vocab else "OTHER"
    return [1.0 if normalized_val == item else 0.0 for item in vocab]


def extract_features(
    envelope: Dict[str, Any],
    beta_lower_bound: float = 0.5,
    sample_size: int = 0,
    health_score: Optional[float] = None,
    occurrence_count: Optional[int] = None,
    attestation_history_rate: Optional[float] = None,
    prior_verification_rate: Optional[float] = None
) -> Tuple[Dict[str, Any], List[float], str]:
    """
    Extracts deterministic numerical and one-hot categorical features
    from an ActionProposedV2 envelope and contextual metadata.
    Returns: (features_dict, feature_vector, feature_hash)
    """
    intents = envelope.get("intents", [])
    first_intent = intents[0] if (intents and isinstance(intents, list) and isinstance(intents[0], dict)) else {}
    target_ref = first_intent.get("target_ref") or envelope.get("target_ref") or {}

    p3_conf = envelope.get("phase3_confidence", {})
    if isinstance(p3_conf, dict):
        score = p3_conf.get("score")
    else:
        score = float(p3_conf) if p3_conf is not None else 0.0
    conf_score = float(score) if score is not None else 0.0

    ev_refs = envelope.get("evidence_refs") or first_intent.get("evidence_refs") or []
    ev_count = len(ev_refs)

    safety_violation = bool(envelope.get("safety_violation", False))
    requires_human_app = bool(first_intent.get("requires_human_approval", False))
    
    intent_type = first_intent.get("intent_type", "NO_SUPPORTED_ACTION")
    mvp_supported = 1.0 if is_mvp_supported(intent_type) else 0.0

    mode = first_intent.get("mode", "OBSERVE")
    is_observe = 1.0 if mode == "OBSERVE" else 0.0
    is_mutative = 1.0 if mode in ["MUTATE_REVERSIBLE", "MUTATE_HIGH_RISK"] else 0.0

    # Authoritative incident context
    inc_ctx = envelope.get("incident_context") or envelope.get("incident_event") or {}
    raw_sev = inc_ctx.get("severity") or envelope.get("severity")
    has_severity = 1.0 if raw_sev is not None else 0.0
    sev_map = {"INFO": 0.0, "LOW": 0.2, "MEDIUM": 0.4, "HIGH": 0.7, "CRITICAL": 1.0}
    severity_norm = sev_map.get(str(raw_sev).upper(), 0.0) if raw_sev is not None else 0.0

    raw_occ = inc_ctx.get("occurrence_count", occurrence_count)
    has_occurrence_count = 1.0 if raw_occ is not None else 0.0
    log_occ_scaled = min(1.0, math.log1p(max(0, raw_occ)) / 10.0) if raw_occ is not None else 0.0

    # Health deficit
    raw_health = inc_ctx.get("current_health_score", health_score)
    has_health_score = 1.0 if raw_health is not None else 0.0
    if raw_health is not None:
        # Scale 0-100 to 0.0-1.0 if > 1
        h_val = float(raw_health) / 100.0 if float(raw_health) > 1.0 else float(raw_health)
        health_deficit = max(0.0, min(1.0, 1.0 - h_val))
    else:
        health_deficit = 0.0

    hist_sample_scaled = min(1.0, max(0, sample_size) / 100.0)

    target_kind = target_ref.get("kind", "container")
    risk_class = first_intent.get("risk_class", "LOW")
    exec_tier = envelope.get("execution_tier", "tier_1")

    # Truthful agent agreement & valid ratios without fake defaults
    agent_valid_ratio = None
    agent_component_agreement = None

    if isinstance(p3_conf, dict):
        if "agent_valid_ratio" in p3_conf and p3_conf["agent_valid_ratio"] is not None:
            agent_valid_ratio = float(p3_conf["agent_valid_ratio"])
        if "agreement_ratio" in p3_conf and p3_conf["agreement_ratio"] is not None:
            agent_component_agreement = float(p3_conf["agreement_ratio"])
        elif "component_agreement" in p3_conf and p3_conf["component_agreement"] is not None:
            agent_component_agreement = float(p3_conf["component_agreement"])

    has_agent_valid_ratio = 1.0 if agent_valid_ratio is not None else 0.0
    has_agent_agreement = 1.0 if agent_component_agreement is not None else 0.0
    has_attestation_history = 1.0 if attestation_history_rate is not None else 0.0
    has_verification_history = 1.0 if prior_verification_rate is not None else 0.0

    # Construct truthful features dictionary
    features_dict = {
        "feature_schema_version": RL_FEATURE_VERSION,
        "phase3_confidence": conf_score,
        "evidence_count_capped": min(ev_count, 10) / 10.0,
        "agent_valid_ratio": agent_valid_ratio,
        "has_agent_valid_ratio": bool(has_agent_valid_ratio),
        "agent_component_agreement": agent_component_agreement,
        "has_agent_agreement": bool(has_agent_agreement),
        "has_health_score": bool(has_health_score),
        "has_occurrence_count": bool(has_occurrence_count),
        "has_severity": bool(has_severity),
        "has_attestation_history": bool(has_attestation_history),
        "has_verification_history": bool(has_verification_history),
        "safety_violation": 1.0 if safety_violation else 0.0,
        "requires_human_approval": 1.0 if requires_human_app else 0.0,
        "mvp_supported": mvp_supported,
        "is_observe_mode": is_observe,
        "is_mutative_mode": is_mutative,
        "severity_normalized": severity_norm if raw_sev is not None else None,
        "health_deficit": health_deficit if raw_health is not None else None,
        "log_occurrence_scaled": log_occ_scaled if raw_occ is not None else None,
        "history_sample_size_scaled": hist_sample_scaled,
        "beta_execution_lower_bound": float(beta_lower_bound),
        "target_attestation_history_rate": attestation_history_rate,
        "prior_verification_rate": prior_verification_rate,
        "intent_type": intent_type,
        "target_kind": target_kind,
        "mode": mode,
        "risk_class": risk_class,
        "execution_tier": exec_tier
    }

    # Deterministic vector construction (23 numerical + 28 categorical = 51 elements)
    vector = [
        conf_score,
        min(ev_count, 10) / 10.0,
        float(agent_valid_ratio) if agent_valid_ratio is not None else 0.0,
        has_agent_valid_ratio,
        float(agent_component_agreement) if agent_component_agreement is not None else 0.0,
        has_agent_agreement,
        1.0 if safety_violation else 0.0,
        1.0 if requires_human_app else 0.0,
        mvp_supported,
        is_observe,
        is_mutative,
        severity_norm,
        has_severity,
        health_deficit,
        has_health_score,
        log_occ_scaled,
        has_occurrence_count,
        hist_sample_scaled,
        float(beta_lower_bound),
        float(attestation_history_rate) if attestation_history_rate is not None else 0.0,
        has_attestation_history,
        float(prior_verification_rate) if prior_verification_rate is not None else 0.0,
        has_verification_history
    ]

    vector.extend(_one_hot(intent_type, CATEGORICAL_VOCAB["capabilities"]))
    vector.extend(_one_hot(target_kind, CATEGORICAL_VOCAB["target_kinds"]))
    vector.extend(_one_hot(mode, CATEGORICAL_VOCAB["modes"]))
    vector.extend(_one_hot(risk_class, CATEGORICAL_VOCAB["risk_classes"]))
    vector.extend(_one_hot(exec_tier, CATEGORICAL_VOCAB["execution_tiers"]))

    # Canonical hash computation
    canonical_json = json.dumps(features_dict, sort_keys=True)
    feature_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    return features_dict, vector, feature_hash
