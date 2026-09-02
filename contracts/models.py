from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from contracts.reason_codes import ReasonCode, TerminalState

@dataclass
class TargetRef:
    kind: str
    canonical_name: str
    namespace: Optional[str] = None
    shadow_alias: Optional[str] = None
    environment_identity: Optional[str] = None

@dataclass
class Intent:
    intent_id: str
    intent_type: str
    mode: str  # OBSERVE | SIMULATE | MUTATE_REVERSIBLE | MUTATE_HIGH_RISK
    target_ref: TargetRef
    parameters: Dict[str, Any]
    evidence_refs: List[str]
    preconditions: List[str]
    postconditions: List[str]
    timeout_seconds: int = 30
    max_attempts: int = 1
    risk_class: str = "LOW"  # LOW | MEDIUM | HIGH | CRITICAL
    requires_human_approval: bool = False
    rollback_intent: Optional[Dict[str, Any]] = None

@dataclass
class SourceRef:
    phase: str
    code_commit: str
    model_name: Optional[str] = None
    prompt_bundle_version: Optional[str] = None
    dataset_version: Optional[str] = None

@dataclass
class Phase3Confidence:
    score: float
    uncertainty: float = 0.0
    calibration_status: str = "UNCALIBRATED"
    calibration_version: str = "v1.0"

@dataclass
class ActionProposedV2Envelope:
    schema_version: str
    event_id: str
    event_type: str
    incident_id: str
    correlation_id: str
    fingerprint: str
    created_at: str
    source: SourceRef
    problem_summary: str
    target_ref: TargetRef
    phase3_confidence: Phase3Confidence
    execution_tier: str
    safety_violation: bool
    evidence_refs: List[str]
    intents: List[Intent]
    human_summary: str

    @classmethod
    def create_default(
        cls,
        incident_id: str,
        problem_summary: str,
        target_name: str,
        intents: List[Intent],
        confidence: float = 0.85,
        correlation_id: Optional[str] = None,
        target_kind: str = "container",
        evidence_refs: Optional[List[str]] = None
    ) -> 'ActionProposedV2Envelope':
        import uuid
        now_iso = datetime.now(timezone.utc).isoformat()
        corr_id = correlation_id or str(uuid.uuid4())
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        
        target = TargetRef(kind=target_kind, canonical_name=target_name, shadow_alias=f"shadow-{target_name}")
        src = SourceRef(phase="phase3_debate", code_commit="2a3867c14af99d003ec8cecd044a01ef874346b8", model_name="qwen2.5-coder")
        conf = Phase3Confidence(score=confidence)
        
        # Aggregate evidence_refs from intents if not provided
        ev_refs = evidence_refs if evidence_refs is not None else []
        if not ev_refs:
            for i in intents:
                ev_refs.extend(i.evidence_refs)
            ev_refs = list(set(ev_refs))
        if not ev_refs:
            ev_refs = ["telemetry_log"]

        return cls(
            schema_version="2.0",
            event_id=event_id,
            event_type="autosre.action.proposed",
            incident_id=incident_id,
            correlation_id=corr_id,
            fingerprint=f"fp_{incident_id}",
            created_at=now_iso,
            source=src,
            problem_summary=problem_summary,
            target_ref=target,
            phase3_confidence=conf,
            execution_tier="tier_1",
            safety_violation=False,
            evidence_refs=ev_refs,
            intents=intents,
            human_summary=problem_summary[:100]
        )

