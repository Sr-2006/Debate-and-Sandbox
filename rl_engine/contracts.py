from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone


@dataclass
class PolicyRef:
    policy_name: str
    policy_version: str
    model_version: str
    operating_mode: str  # SHADOW | ADVISORY


@dataclass
class ProposalRef:
    intent_type: str
    target_kind: str
    mode: str
    risk_class: str


@dataclass
class RLAdvisoryData:
    schema_version: str
    advisory_id: str
    incident_id: str
    run_id: str
    created_at: str
    policy: PolicyRef
    proposal: ProposalRef
    recommendation: str  # ACCEPT_PROPOSAL | OBSERVE_FIRST | REQUIRE_HUMAN_REVIEW | ABSTAIN
    action_scores: Dict[str, Optional[float]]
    uncertainty: float
    sample_size: int
    cold_start: bool
    influence_allowed: bool
    reason_codes: List[str]
    feature_schema_version: str
    feature_hash: str
    latency_ms: float
    estimated_success_probability: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "advisory_id": self.advisory_id,
            "incident_id": self.incident_id,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "policy": {
                "policy_name": self.policy.policy_name,
                "policy_version": self.policy.policy_version,
                "model_version": self.policy.model_version,
                "operating_mode": self.policy.operating_mode
            },
            "proposal": {
                "intent_type": self.proposal.intent_type,
                "target_kind": self.proposal.target_kind,
                "mode": self.proposal.mode,
                "risk_class": self.proposal.risk_class
            },
            "recommendation": self.recommendation,
            "action_scores": self.action_scores,
            "estimated_success_probability": self.estimated_success_probability,
            "uncertainty": self.uncertainty,
            "sample_size": self.sample_size,
            "cold_start": self.cold_start,
            "influence_allowed": self.influence_allowed,
            "reason_codes": self.reason_codes,
            "feature_schema_version": self.feature_schema_version,
            "feature_hash": self.feature_hash,
            "latency_ms": self.latency_ms
        }


@dataclass
class EpisodeContext:
    feature_schema_version: str
    features: Dict[str, Any]
    feature_vector: List[float]
    feature_hash: str


@dataclass
class Phase4Outcome:
    status: str
    simulated: bool
    attested: bool
    execution_success: bool
    verification_passed: bool
    rollback_attempted: bool
    rollback_confirmed: bool


@dataclass
class EpisodeLearning:
    eligible: bool
    eligibility_reason: str
    reward: Optional[float]
    sample_weight: float
    behavior_action: str
    behavior_propensity: Optional[float] = None


@dataclass
class LearningEpisodeData:
    schema_version: str
    episode_id: str
    incident_id: str
    run_id: str
    payload_hash: str
    created_at: str
    context: EpisodeContext
    proposal: ProposalRef
    advisory: Dict[str, Any]
    phase4: Phase4Outcome
    learning: EpisodeLearning

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "incident_id": self.incident_id,
            "run_id": self.run_id,
            "payload_hash": self.payload_hash,
            "created_at": self.created_at,
            "context": {
                "feature_schema_version": self.context.feature_schema_version,
                "features": self.context.features,
                "feature_vector": self.context.feature_vector,
                "feature_hash": self.context.feature_hash
            },
            "proposal": {
                "intent_type": self.proposal.intent_type,
                "target_kind": self.proposal.target_kind,
                "mode": self.proposal.mode,
                "risk_class": self.proposal.risk_class
            },
            "advisory": self.advisory,
            "phase4": {
                "status": self.phase4.status,
                "simulated": self.phase4.simulated,
                "attested": self.phase4.attested,
                "execution_success": self.phase4.execution_success,
                "verification_passed": self.phase4.verification_passed,
                "rollback_attempted": self.phase4.rollback_attempted,
                "rollback_confirmed": self.phase4.rollback_confirmed
            },
            "learning": {
                "eligible": self.learning.eligible,
                "eligibility_reason": self.learning.eligibility_reason,
                "reward": self.learning.reward,
                "sample_weight": self.learning.sample_weight,
                "behavior_action": self.learning.behavior_action,
                "behavior_propensity": self.learning.behavior_propensity
            }
        }
