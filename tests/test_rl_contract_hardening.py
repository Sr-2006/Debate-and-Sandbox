import json
import os
import copy
import pytest
from contracts.validation import validate_envelope, ReasonCode, get_capabilities, is_mvp_supported
from contracts.models import ActionProposedV2Envelope, Intent, TargetRef, Phase3Confidence, get_runtime_git_commit, get_runtime_model_name
from debate.action_publisher import build_action_proposed
from rl_engine.advisor import RLAdvisor
from rl_engine.episode_builder import build_learning_episode
from rl_engine.feature_extractor import extract_features
from rl_engine.safety_mask import get_allowed_actions
from rl_engine.contracts import FeatureSnapshot, RLAdvisoryData, PolicyRef, ProposalRef
from jsonschema import validate, ValidationError


def test_feature_vector_frozen_snapshot_consistency():
    """Verify RL decision feature vector matches learning episode feature vector byte-for-byte."""
    envelope = {
        "schema_version": "2.0",
        "incident_id": "case_freeze_test",
        "incident_context": {
            "severity": "HIGH",
            "occurrence_count": 12,
            "priority_score": 0.91
        },
        "phase3_confidence": {
            "score": 0.85,
            "agreement_ratio": 0.80
        },
        "safety_violation": False,
        "evidence_refs": ["log_ref_1"],
        "execution_tier": "tier_1",
        "intents": [
            {
                "intent_id": "int_1",
                "intent_type": "container.restart",
                "mode": "MUTATE_REVERSIBLE",
                "risk_class": "MEDIUM",
                "requires_human_approval": False,
                "target_ref": {"kind": "container", "canonical_name": "user-service"},
                "parameters": {},
                "evidence_refs": ["log_ref_1"],
                "preconditions": [],
                "postconditions": [],
                "timeout_seconds": 30,
                "max_attempts": 1
            }
        ],
        "target_ref": {"kind": "container", "canonical_name": "user-service"}
    }

    advisor = RLAdvisor(model_version="cold-start")
    advisory = advisor.generate_advisory(envelope)

    # Confirm advisory retained frozen snapshot
    assert advisory.feature_snapshot is not None
    assert advisory.feature_snapshot.features is not None
    assert advisory.feature_snapshot.feature_vector is not None
    assert advisory.feature_snapshot.feature_hash is not None

    p4_result = {
        "status": "SANDBOX_VERIFIED",
        "attestation": {"attested": True},
        "execution": {"result": {"success": True}},
        "verification": {"passed": True},
        "rollback": {"attempted": False}
    }

    episode = build_learning_episode(
        advisory=advisory,
        envelope=envelope,
        phase4_result=p4_result
    )

    # Invariant: Feature vector, dictionary, and hash in episode must match decision advisory exactly
    assert episode.context.feature_vector == advisory.feature_snapshot.feature_vector
    assert episode.context.features == advisory.feature_snapshot.features
    assert episode.context.feature_hash == advisory.feature_snapshot.feature_hash


def test_missing_feature_snapshot_ineligible_for_features_v2():
    """Verify that a features-v2 advisory missing a feature snapshot is strictly not eligible for training."""
    advisory_without_snapshot = RLAdvisoryData(
        schema_version="1.0",
        advisory_id="adv_no_snap",
        incident_id="case_no_snap",
        run_id="run_test",
        created_at="2026-09-04T00:00:00Z",
        policy=PolicyRef("safe_disjoint_linucb", "rl-mvp-1", "cold-start", "SHADOW"),
        proposal=ProposalRef("container.restart", "container", "MUTATE_REVERSIBLE", "MEDIUM"),
        recommendation="ABSTAIN",
        action_scores={"ACCEPT_PROPOSAL": None, "OBSERVE_FIRST": None, "REQUIRE_HUMAN_REVIEW": None, "ABSTAIN": 0.0},
        uncertainty=0.5,
        sample_size=0,
        cold_start=True,
        influence_allowed=False,
        reason_codes=["TEST"],
        feature_schema_version="features-v2",
        feature_hash="hash",
        latency_ms=1.0,
        feature_snapshot=None
    )

    envelope = {
        "schema_version": "2.0",
        "incident_id": "case_test",
        "incident_context": {"severity": "HIGH", "occurrence_count": 1, "priority_score": 0.5},
        "target_ref": {"kind": "container", "canonical_name": "user-service"},
        "intents": [{"intent_type": "container.restart", "mode": "MUTATE_REVERSIBLE", "risk_class": "MEDIUM", "requires_human_approval": False}]
    }

    p4_result = {
        "status": "SANDBOX_VERIFIED",
        "attestation": {"attested": True},
        "execution": {"result": {"success": True}},
        "verification": {"passed": True},
        "rollback": {"attempted": False}
    }

    episode = build_learning_episode(
        advisory=advisory_without_snapshot,
        envelope=envelope,
        phase4_result=p4_result
    )

    assert episode.learning.eligible is False
    assert episode.learning.eligibility_reason == "FEATURE_SNAPSHOT_MISSING"
    assert episode.learning.sample_weight == 0.0
    assert episode.learning.reward is None


def test_target_unresolved_hard_mask():
    """Verify target_resolved=False forces ABSTAIN and RL_TARGET_UNRESOLVED."""
    actions, reasons = get_allowed_actions(
        p3_status="SUCCESS",
        confidence=0.9,
        safety_violation=False,
        mode="MUTATE_REVERSIBLE",
        human_approval=False,
        capability_mapped=True,
        mvp_supported=True,
        evidence_refs=["log_1"],
        target_resolved=False
    )
    assert actions == ["ABSTAIN"]
    assert "RL_TARGET_UNRESOLVED" in reasons

    # Test via Advisor with unresolved target name
    unresolved_envelope = {
        "incident_id": "case_unresolved",
        "target_ref": {"kind": "container", "canonical_name": "unknown"},
        "intents": [
            {
                "intent_type": "container.restart",
                "target_ref": {"kind": "container", "canonical_name": "unknown"},
                "mode": "MUTATE_REVERSIBLE",
                "evidence_refs": ["log_1"]
            }
        ]
    }
    advisor = RLAdvisor()
    advisory = advisor.generate_advisory(unresolved_envelope)
    assert advisory.recommendation == "ABSTAIN"
    assert "RL_TARGET_UNRESOLVED" in advisory.reason_codes


def test_no_fake_optimistic_feature_defaults():
    """Verify feature extractor does not inject 1.0 / 0.95 defaults when unobserved."""
    envelope = {
        "incident_id": "case_raw",
        "phase3_confidence": {"score": 0.70},  # agreement not provided
        "intents": [
            {
                "intent_type": "container.restart",
                "mode": "MUTATE_REVERSIBLE",
                "risk_class": "MEDIUM",
                "target_ref": {"kind": "container", "canonical_name": "auth-service"}
            }
        ]
    }

    f_dict, f_vec, f_hash = extract_features(envelope)
    assert f_dict["agent_valid_ratio"] is None
    assert f_dict["agent_component_agreement"] is None
    assert f_dict["target_attestation_history_rate"] is None
    assert f_dict["prior_verification_rate"] is None
    assert f_dict["has_agent_agreement"] is False
    assert f_dict["has_attestation_history"] is False
    assert f_dict["has_verification_history"] is False


def test_observed_zero_differs_from_missing():
    """Verify observed 0.0 is distinct from unobserved None via has_* flags."""
    env_unobserved = {
        "incident_id": "case_1",
        "phase3_confidence": {"score": 0.8},
        "intents": [{"intent_type": "container.restart", "mode": "MUTATE_REVERSIBLE", "risk_class": "MEDIUM"}]
    }
    f1_dict, _, _ = extract_features(env_unobserved)
    assert f1_dict["agent_component_agreement"] is None
    assert f1_dict["has_agent_agreement"] is False

    env_observed_zero = {
        "incident_id": "case_2",
        "phase3_confidence": {"score": 0.8, "agreement_ratio": 0.0},
        "intents": [{"intent_type": "container.restart", "mode": "MUTATE_REVERSIBLE", "risk_class": "MEDIUM"}]
    }
    f2_dict, _, _ = extract_features(env_observed_zero)
    assert f2_dict["agent_component_agreement"] == 0.0
    assert f2_dict["has_agent_agreement"] is True


def test_authoritative_capabilities_truth():
    """Verify RL advisor uses catalog capabilities instead of hardcoded lists."""
    capabilities = get_capabilities()
    assert "container.restart" in capabilities
    assert is_mvp_supported("container.restart") is True
    assert is_mvp_supported("storage.snapshot.restore") is False


def test_conservative_validation_rules():
    """Verify validation rejects intent downgrading risk class or disabling human approval."""
    # 1. Downgrade risk class (postgres.wal.archive_cleanup is HIGH in catalog)
    downgraded_envelope = {
        "schema_version": "2.0",
        "event_id": "evt_test",
        "event_type": "autosre.action.proposed",
        "incident_id": "case_test",
        "correlation_id": "corr_test",
        "fingerprint": "fp_test",
        "created_at": "2026-09-04T00:00:00Z",
        "source": {"phase": "debate", "code_commit": "abc"},
        "problem_summary": "test",
        "target_ref": {"kind": "container", "canonical_name": "postgres-db"},
        "phase3_confidence": {"score": 0.9},
        "execution_tier": "tier_1",
        "safety_violation": False,
        "evidence_refs": ["log_1"],
        "incident_context": {
            "severity": "HIGH",
            "priority_score": 0.9,
            "occurrence_count": 5
        },
        "intents": [
            {
                "intent_id": "int_1",
                "intent_type": "postgres.wal.archive_cleanup",
                "mode": "MUTATE_HIGH_RISK",
                "target_ref": {"kind": "container", "canonical_name": "postgres-db"},
                "parameters": {"retention_boundary": "1d"},
                "evidence_refs": ["log_1"],
                "preconditions": [],
                "postconditions": [],
                "timeout_seconds": 30,
                "max_attempts": 1,
                "risk_class": "LOW",  # Catalog requires HIGH
                "requires_human_approval": True
            }
        ],
        "human_summary": "test"
    }
    is_valid, errors, code = validate_envelope(downgraded_envelope)
    assert not is_valid
    assert code == ReasonCode.BLOCKED_INVALID_PARAMETERS

    # 2. More conservative risk class is allowed (container.restart catalog is MEDIUM, intent is HIGH)
    conservative_envelope = json.loads(json.dumps(downgraded_envelope))
    conservative_envelope["intents"][0]["intent_type"] = "container.restart"
    conservative_envelope["intents"][0]["mode"] = "MUTATE_REVERSIBLE"
    conservative_envelope["intents"][0]["parameters"] = {}
    conservative_envelope["intents"][0]["risk_class"] = "HIGH"  # More conservative than catalog MEDIUM
    conservative_envelope["intents"][0]["requires_human_approval"] = False
    is_valid, errors, code = validate_envelope(conservative_envelope)
    assert is_valid

    # 3. Disable required human approval (catalog requires_human_approval=True)
    no_approval_envelope = json.loads(json.dumps(downgraded_envelope))
    no_approval_envelope["intents"][0]["risk_class"] = "HIGH"
    no_approval_envelope["intents"][0]["requires_human_approval"] = False
    is_valid, errors, code = validate_envelope(no_approval_envelope)
    assert not is_valid
    assert code == ReasonCode.BLOCKED_INVALID_PARAMETERS


def test_build_action_proposed_carries_incident_context():
    """Verify build_action_proposed carries authoritative incident_context."""
    raw_problem = {
        "incident_event": {
            "incident_id": "case_auth_ctx",
            "target_service": "user-service",
            "severity": "CRITICAL",
            "priority_score": 95.0,
            "occurrence_count": 420
        },
        "system_context": {
            "current_health_score": 25
        }
    }
    debate_result = {
        "confidence_score": 0.88,
        "original_problem": raw_problem,
        "solution": {
            "primary_component": "user-service",
            "intent": {
                "intent_type": "container.restart",
                "mode": "MUTATE_REVERSIBLE",
                "risk_class": "MEDIUM",
                "requires_human_approval": False,
                "parameters": {},
                "evidence_refs": ["ev_1"]
            }
        }
    }
    envelope = build_action_proposed("case_auth_ctx", debate_result)
    assert "incident_context" in envelope
    assert envelope["incident_context"]["severity"] == "CRITICAL"
    assert envelope["incident_context"]["priority_score"] == 95.0
    assert envelope["incident_context"]["occurrence_count"] == 420
    assert envelope["incident_context"]["current_health_score"] == 25.0


def test_dynamic_provenance():
    """Verify git commit and model name provenance are dynamically resolved, not hardcoded."""
    commit = get_runtime_git_commit()
    assert commit and len(commit) >= 7
    model = get_runtime_model_name()
    assert model and "qwen" in model


def test_rl_feature_version_defaults():
    """Verify RL_FEATURE_VERSION defaults to features-v2 and dimension is 51."""
    from rl_engine.config import RL_FEATURE_VERSION, RL_FEATURE_DIMENSION
    assert RL_FEATURE_VERSION == "features-v2"
    assert RL_FEATURE_DIMENSION == 51


def test_missing_severity_distinct_from_info():
    """Verify missing severity (norm=0.0, has=0.0) != observed INFO (norm=0.0, has=1.0)."""
    base_env = {
        "incident_id": "case_sev",
        "phase3_confidence": {"score": 0.8},
        "intents": [{"intent_type": "container.restart", "mode": "MUTATE_REVERSIBLE"}]
    }

    # Missing severity
    env_missing = copy.deepcopy(base_env)
    f_missing, vec_missing, _ = extract_features(env_missing)
    assert f_missing["has_severity"] is False
    assert f_missing["severity_normalized"] is None
    # Index 11 is severity_norm, Index 12 is has_severity
    assert vec_missing[11] == 0.0
    assert vec_missing[12] == 0.0

    # Actual INFO severity
    env_info = copy.deepcopy(base_env)
    env_info["incident_context"] = {"severity": "INFO"}
    f_info, vec_info, _ = extract_features(env_info)
    assert f_info["has_severity"] is True
    assert f_info["severity_normalized"] == 0.0
    assert vec_info[11] == 0.0
    assert vec_info[12] == 1.0

    # Vectors must not be equal
    assert vec_missing != vec_info


def test_missing_health_distinct_from_perfect_health():
    """Verify missing health (deficit=0.0, has=0.0) != perfect health (deficit=0.0, has=1.0)."""
    base_env = {
        "incident_id": "case_health",
        "phase3_confidence": {"score": 0.8},
        "intents": [{"intent_type": "container.restart", "mode": "MUTATE_REVERSIBLE"}]
    }

    # Missing health
    env_missing = copy.deepcopy(base_env)
    f_missing, vec_missing, _ = extract_features(env_missing)
    assert f_missing["has_health_score"] is False
    assert f_missing["health_deficit"] is None
    # Index 13 is health_deficit, Index 14 is has_health_score
    assert vec_missing[13] == 0.0
    assert vec_missing[14] == 0.0

    # Perfect health (100.0 or 1.0 -> deficit 0.0)
    env_perf = copy.deepcopy(base_env)
    env_perf["incident_context"] = {"current_health_score": 100.0}
    f_perf, vec_perf, _ = extract_features(env_perf)
    assert f_perf["has_health_score"] is True
    assert f_perf["health_deficit"] == 0.0
    assert vec_perf[13] == 0.0
    assert vec_perf[14] == 1.0

    # Vectors must not be equal
    assert vec_missing != vec_perf


def test_missing_occurrence_distinct_from_observed_zero():
    """Verify missing occurrence (scaled=0.0, has=0.0) != observed 0 (scaled=0.0, has=1.0)."""
    base_env = {
        "incident_id": "case_occ",
        "phase3_confidence": {"score": 0.8},
        "intents": [{"intent_type": "container.restart", "mode": "MUTATE_REVERSIBLE"}]
    }

    # Missing occurrence count
    env_missing = copy.deepcopy(base_env)
    f_missing, vec_missing, _ = extract_features(env_missing)
    assert f_missing["has_occurrence_count"] is False
    assert f_missing["log_occurrence_scaled"] is None
    # Index 15 is log_occ_scaled, Index 16 is has_occurrence_count
    assert vec_missing[15] == 0.0
    assert vec_missing[16] == 0.0

    # Observed 0 occurrence count
    env_zero = copy.deepcopy(base_env)
    env_zero["incident_context"] = {"occurrence_count": 0}
    f_zero, vec_zero, _ = extract_features(env_zero)
    assert f_zero["has_occurrence_count"] is True
    assert f_zero["log_occurrence_scaled"] == 0.0
    assert vec_zero[15] == 0.0
    assert vec_zero[16] == 1.0

    # Vectors must not be equal
    assert vec_missing != vec_zero


def test_missing_agreement_distinct_from_observed_zero():
    """Verify missing agreement (val=0.0, has=0.0) != observed 0.0 (val=0.0, has=1.0)."""
    base_env = {
        "incident_id": "case_agr",
        "phase3_confidence": {"score": 0.8},
        "intents": [{"intent_type": "container.restart", "mode": "MUTATE_REVERSIBLE"}]
    }

    # Missing agreement
    env_missing = copy.deepcopy(base_env)
    f_missing, vec_missing, _ = extract_features(env_missing)
    assert f_missing["has_agent_agreement"] is False
    assert f_missing["agent_component_agreement"] is None
    # Index 4 is agent_agreement_val, Index 5 is has_agent_agreement
    assert vec_missing[4] == 0.0
    assert vec_missing[5] == 0.0

    # Observed 0.0 agreement
    env_zero = copy.deepcopy(base_env)
    env_zero["phase3_confidence"] = {"score": 0.8, "agreement_ratio": 0.0}
    f_zero, vec_zero, _ = extract_features(env_zero)
    assert f_zero["has_agent_agreement"] is True
    assert f_zero["agent_component_agreement"] == 0.0
    assert vec_zero[4] == 0.0
    assert vec_zero[5] == 1.0

    # Vectors must not be equal
    assert vec_missing != vec_zero


def test_vector_contains_all_has_indicators():
    """Verify vector contains all 7 explicit has_* missingness indicator dimensions."""
    envelope = {
        "incident_id": "case_indicators",
        "phase3_confidence": {"score": 0.85, "agent_valid_ratio": 1.0, "agreement_ratio": 0.9},
        "incident_context": {"severity": "HIGH", "occurrence_count": 5, "current_health_score": 80.0},
        "intents": [{"intent_type": "container.restart", "mode": "MUTATE_REVERSIBLE"}]
    }

    f_dict, vec, _ = extract_features(
        envelope,
        attestation_history_rate=1.0,
        prior_verification_rate=1.0
    )

    assert len(vec) == 51
    # Check boolean flags in features dict
    assert f_dict["has_agent_valid_ratio"] is True
    assert f_dict["has_agent_agreement"] is True
    assert f_dict["has_health_score"] is True
    assert f_dict["has_occurrence_count"] is True
    assert f_dict["has_severity"] is True
    assert f_dict["has_attestation_history"] is True
    assert f_dict["has_verification_history"] is True

    # Check indicator vector positions:
    # 3: has_agent_valid_ratio
    # 5: has_agent_agreement
    # 12: has_severity
    # 14: has_health_score
    # 16: has_occurrence_count
    # 20: has_attestation_history
    # 22: has_verification_history
    assert vec[3] == 1.0
    assert vec[5] == 1.0
    assert vec[12] == 1.0
    assert vec[14] == 1.0
    assert vec[16] == 1.0
    assert vec[20] == 1.0
    assert vec[22] == 1.0


def test_v1_model_refuses_v2_feature_vector_safely():
    """Verify model trained on v1 schema/dimension triggers MODEL_FEATURE_SCHEMA_MISMATCH and cold-start fallback."""
    from rl_engine.policy import SafeDisjointLinUCB
    advisor = RLAdvisor(operating_mode="ADVISORY")

    # Simulate loaded model having features-v1 schema and 44 dimension
    advisor.model_version = "v1-legacy-promoted"
    advisor.policy = SafeDisjointLinUCB(feature_dim=44)
    advisor.model_meta = {
        "model_version": "v1-legacy-promoted",
        "feature_schema_version": "features-v1",
        "feature_dimension": 44,
        "cold_start": False
    }

    envelope = {
        "incident_id": "case_v1_refusal",
        "phase3_confidence": {"score": 0.90},
        "target_ref": {"kind": "container", "canonical_name": "user-service"},
        "intents": [
            {
                "intent_type": "container.restart",
                "mode": "MUTATE_REVERSIBLE",
                "risk_class": "MEDIUM",
                "requires_human_approval": False,
                "target_ref": {"kind": "container", "canonical_name": "user-service"}
            }
        ]
    }

    advisory = advisor.generate_advisory(envelope)
    assert advisory.cold_start is True
    assert "MODEL_FEATURE_SCHEMA_MISMATCH" in advisory.reason_codes
    assert "RL_COLD_START" in advisory.reason_codes
    assert advisory.influence_allowed is False
