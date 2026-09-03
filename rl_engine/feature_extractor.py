import math
import hashlib
import json
from typing import Dict, Any, Tuple, List
from rl_engine.config import CATEGORICAL_VOCAB, RL_FEATURE_VERSION


def _one_hot(value: str, vocab: List[str]) -> List[float]:
    normalized_val = value if value in vocab else "OTHER"
    return [1.0 if normalized_val == item else 0.0 for item in vocab]


def extract_features(
    envelope: Dict[str, Any],
    beta_lower_bound: float = 0.5,
    sample_size: int = 0,
    health_score: float = 1.0,
    occurrence_count: int = 1
) -> Tuple[Dict[str, Any], List[float], str]:
    """
    Extracts deterministic numerical and one-hot categorical features (features-v1)
    from an ActionProposedV2 envelope and contextual metadata.
    Returns: (features_dict, feature_vector, feature_hash)
    """
    intents = envelope.get("intents", [])
    first_intent = intents[0] if intents else {}
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
    mvp_supported = 1.0 if intent_type in [
        "container.restart",
        "postgres.setting.update",
        "redis.eviction_policy.update",
        "observe.logs.search"
    ] else 0.0

    mode = first_intent.get("mode", "OBSERVE")
    is_observe = 1.0 if mode == "OBSERVE" else 0.0
    is_mutative = 1.0 if mode in ["MUTATE_REVERSIBLE", "MUTATE_HIGH_RISK"] else 0.0

    severity_str = envelope.get("severity", "MEDIUM").upper()
    sev_map = {"INFO": 0.0, "LOW": 0.2, "MEDIUM": 0.4, "HIGH": 0.7, "CRITICAL": 1.0}
    severity_norm = sev_map.get(severity_str, 0.4)

    health_deficit = max(0.0, min(1.0, 1.0 - float(health_score)))
    log_occ_scaled = min(1.0, math.log1p(max(0, occurrence_count)) / 10.0)
    hist_sample_scaled = min(1.0, max(0, sample_size) / 100.0)

    target_kind = target_ref.get("kind", "container")
    risk_class = first_intent.get("risk_class", "LOW")
    exec_tier = envelope.get("execution_tier", "tier_1")

    # Construct features dictionary
    features_dict = {
        "feature_schema_version": RL_FEATURE_VERSION,
        "phase3_confidence": conf_score,
        "evidence_count_capped": min(ev_count, 10) / 10.0,
        "agent_valid_ratio": 1.0,  # 3/3 valid in default orchestrator run
        "agent_component_agreement": p3_conf.get("agreement_ratio", 0.95) if isinstance(p3_conf, dict) else 0.95,
        "safety_violation": 1.0 if safety_violation else 0.0,
        "requires_human_approval": 1.0 if requires_human_app else 0.0,
        "mvp_supported": mvp_supported,
        "is_observe_mode": is_observe,
        "is_mutative_mode": is_mutative,
        "severity_normalized": severity_norm,
        "health_deficit": health_deficit,
        "log_occurrence_scaled": log_occ_scaled,
        "history_sample_size_scaled": hist_sample_scaled,
        "beta_execution_lower_bound": float(beta_lower_bound),
        "target_attestation_history_rate": 1.0,
        "prior_verification_rate": 1.0,
        "intent_type": intent_type,
        "target_kind": target_kind,
        "mode": mode,
        "risk_class": risk_class,
        "execution_tier": exec_tier
    }

    # Vector construction (16 numerical + 28 categorical = 44 elements)
    vector = [
        conf_score,
        min(ev_count, 10) / 10.0,
        1.0,
        float(features_dict["agent_component_agreement"]),
        1.0 if safety_violation else 0.0,
        1.0 if requires_human_app else 0.0,
        mvp_supported,
        is_observe,
        is_mutative,
        severity_norm,
        health_deficit,
        log_occ_scaled,
        hist_sample_scaled,
        float(beta_lower_bound),
        1.0,
        1.0
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
