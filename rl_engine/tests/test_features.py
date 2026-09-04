from rl_engine.config import RL_FEATURE_VERSION, RL_FEATURE_DIMENSION
from rl_engine.feature_extractor import extract_features


def test_feature_extractor_vector_length_and_determinism():
    envelope = {
        "incident_id": "case_test",
        "phase3_confidence": {"score": 0.85, "agreement_ratio": 0.95},
        "safety_violation": False,
        "evidence_refs": ["log_1"],
        "execution_tier": "tier_1",
        "intents": [
            {
                "intent_type": "container.restart",
                "mode": "MUTATE_REVERSIBLE",
                "risk_class": "LOW",
                "requires_human_approval": False,
                "target_ref": {"kind": "container", "canonical_name": "app"}
            }
        ]
    }

    f1_dict, f1_vec, f1_hash = extract_features(envelope, beta_lower_bound=0.7, sample_size=10)
    f2_dict, f2_vec, f2_hash = extract_features(envelope, beta_lower_bound=0.7, sample_size=10)

    # Invariants
    assert len(f1_vec) == RL_FEATURE_DIMENSION
    assert len(f1_vec) == 51
    assert f1_hash == f2_hash
    assert f1_vec == f2_vec
    assert f1_dict["feature_schema_version"] == "features-v2"


def test_feature_extractor_unknown_categories_map_to_other():
    envelope = {
        "incident_id": "case_unknown",
        "phase3_confidence": {"score": 0.5},
        "intents": [
            {
                "intent_type": "unknown.custom.capability",
                "mode": "CUSTOM_MODE",
                "risk_class": "CUSTOM_RISK",
                "target_ref": {"kind": "custom_kind"}
            }
        ]
    }

    f_dict, f_vec, f_hash = extract_features(envelope)
    assert len(f_vec) == 51
    assert f_dict["intent_type"] == "unknown.custom.capability"
