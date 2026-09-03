import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from rl_engine.config import RL_FEATURE_VERSION
from rl_engine.contracts import LearningEpisodeData, EpisodeContext, ProposalRef, Phase4Outcome, EpisodeLearning, RLAdvisoryData
from rl_engine.feature_extractor import extract_features
from rl_engine.reward import evaluate_outcome_reward
from contracts.canonical_json import compute_payload_hash


def build_learning_episode(
    advisory: Any,
    envelope: Dict[str, Any],
    phase4_result: Dict[str, Any],
    run_id: Optional[str] = None
) -> LearningEpisodeData:
    """
    Constructs a LearningEpisodeData record from an advisory, input envelope, and Phase 4 execution outcome.
    """
    episode_id = f"ep_{uuid.uuid4().hex[:12]}"
    incident_id = envelope.get("incident_id", "case_unknown")
    effective_run_id = run_id or (advisory.run_id if hasattr(advisory, "run_id") else envelope.get("run_id", f"run_{uuid.uuid4().hex[:8]}"))
    payload_hash = envelope.get("payload_hash") or compute_payload_hash(envelope)

    intents = envelope.get("intents", [])
    first_intent = intents[0] if intents else {}
    intent_type = first_intent.get("intent_type", "NO_SUPPORTED_ACTION")
    mode = first_intent.get("mode", "OBSERVE")
    risk_class = first_intent.get("risk_class", "LOW")
    target_ref = first_intent.get("target_ref") or envelope.get("target_ref") or {}
    target_kind = target_ref.get("kind", "container")

    # Extract context features
    features_dict, feature_vector, feature_hash = extract_features(envelope)

    context = EpisodeContext(
        feature_schema_version=RL_FEATURE_VERSION,
        features=features_dict,
        feature_vector=feature_vector,
        feature_hash=feature_hash
    )

    proposal = ProposalRef(
        intent_type=intent_type,
        target_kind=target_kind,
        mode=mode,
        risk_class=risk_class
    )

    advisory_dict = advisory.to_dict() if hasattr(advisory, "to_dict") else (advisory if isinstance(advisory, dict) else {})

    status = phase4_result.get("status", "NOT_RUN")
    simulated = bool(phase4_result.get("simulated", False) or status == "SIMULATION_VERIFIED")
    attestation = phase4_result.get("attestation", {})
    attested = bool(attestation.get("attested", False))
    execution = phase4_result.get("execution", {})
    exec_res = execution.get("result", {})
    exec_success = bool(exec_res.get("success", False)) if isinstance(exec_res, dict) else bool(exec_res)
    verification = phase4_result.get("verification", {})
    ver_passed = bool(verification.get("passed", False))
    rollback = phase4_result.get("rollback", {})
    rb_attempted = bool(rollback.get("attempted", False))
    rb_res = rollback.get("result", {})
    rb_confirmed = bool(rb_res.get("success", False)) if isinstance(rb_res, dict) else bool(rb_res)

    p4_outcome = Phase4Outcome(
        status=status,
        simulated=simulated,
        attested=attested,
        execution_success=exec_success,
        verification_passed=ver_passed,
        rollback_attempted=rb_attempted,
        rollback_confirmed=rb_confirmed
    )

    # Evaluate Reward & Eligibility
    eligible, eligibility_reason, reward, sample_weight = evaluate_outcome_reward(phase4_result)

    # Determine behavior action executed by system
    if status in ["SANDBOX_VERIFIED", "SIMULATION_VERIFIED"]:
        behavior_action = "ACCEPT_PROPOSAL"
    elif status == "READ_ONLY_OBSERVED":
        behavior_action = "OBSERVE_FIRST"
    elif status == "HUMAN_REVIEW_REQUIRED":
        behavior_action = "REQUIRE_HUMAN_REVIEW"
    else:
        behavior_action = "ABSTAIN"

    learning = EpisodeLearning(
        eligible=eligible,
        eligibility_reason=eligibility_reason,
        reward=reward,
        sample_weight=sample_weight,
        behavior_action=behavior_action,
        behavior_propensity=None
    )

    return LearningEpisodeData(
        schema_version="1.0",
        episode_id=episode_id,
        incident_id=incident_id,
        run_id=effective_run_id,
        payload_hash=payload_hash,
        created_at=datetime.now(timezone.utc).isoformat(),
        context=context,
        proposal=proposal,
        advisory=advisory_dict,
        phase4=p4_outcome,
        learning=learning
    )
