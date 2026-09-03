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
