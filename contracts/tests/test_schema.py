import pytest
from contracts.models import ActionProposedV2Envelope, Intent, TargetRef
from contracts.validation import validate_envelope
from contracts.reason_codes import ReasonCode

def test_valid_envelope():
    intent = Intent(
        intent_id="int_01",
        intent_type="postgres.setting.update",
        mode="MUTATE_REVERSIBLE",
        target_ref=TargetRef(kind="container", canonical_name="postgres-db"),
        parameters={"setting_name": "max_connections", "value": 200},
        evidence_refs=["log_01"],
        preconditions=["db_reachable"],
        postconditions=["setting_equals_200"],
        timeout_seconds=30,
        max_attempts=1,
        risk_class="MEDIUM",
        requires_human_approval=False
    )
    env = ActionProposedV2Envelope.create_default(
        incident_id="case_04",
        problem_summary="PostgreSQL lock timeout issue",
        target_name="postgres-db",
        intents=[intent],
        confidence=0.9
    )
    
    # Convert dataclass to dict representation for schema validation
    dict_repr = {
        "schema_version": env.schema_version,
        "event_id": env.event_id,
        "event_type": env.event_type,
        "incident_id": env.incident_id,
        "correlation_id": env.correlation_id,
        "fingerprint": env.fingerprint,
        "created_at": env.created_at,
        "source": {
            "phase": env.source.phase,
            "code_commit": env.source.code_commit,
            "model_name": env.source.model_name
        },
        "problem_summary": env.problem_summary,
        "target_ref": {
            "kind": env.target_ref.kind,
            "canonical_name": env.target_ref.canonical_name,
            "shadow_alias": env.target_ref.shadow_alias
        },
        "phase3_confidence": {
            "score": env.phase3_confidence.score,
            "uncertainty": env.phase3_confidence.uncertainty,
            "calibration_status": env.phase3_confidence.calibration_status
        },
        "execution_tier": env.execution_tier,
        "safety_violation": env.safety_violation,
        "evidence_refs": env.evidence_refs,
        "intents": [
            {
                "intent_id": i.intent_id,
                "intent_type": i.intent_type,
                "mode": i.mode,
                "target_ref": {
                    "kind": i.target_ref.kind,
                    "canonical_name": i.target_ref.canonical_name
                },
                "parameters": i.parameters,
                "evidence_refs": i.evidence_refs,
                "preconditions": i.preconditions,
                "postconditions": i.postconditions,
                "timeout_seconds": i.timeout_seconds,
                "max_attempts": i.max_attempts,
                "risk_class": i.risk_class,
                "requires_human_approval": i.requires_human_approval
            } for i in env.intents
        ],
        "human_summary": env.human_summary
    }

    is_valid, errors, code = validate_envelope(dict_repr)
    assert is_valid is True, f"Validation failed: {errors}"


def test_invalid_placeholder_envelope():
    intent = Intent(
        intent_id="int_02",
        intent_type="workload.resources.patch",
        mode="MUTATE_REVERSIBLE",
        target_ref=TargetRef(kind="workload", canonical_name="<namespace>"),
        parameters={"resource_type": "cpu", "limit_value": "path/to/manifest"},
        evidence_refs=["log_01"],
        preconditions=[],
        postconditions=[],
        timeout_seconds=30,
        max_attempts=1,
        risk_class="MEDIUM",
        requires_human_approval=False
    )
    env = ActionProposedV2Envelope.create_default(
        incident_id="case_15",
        problem_summary="CPU throttling",
        target_name="auth-service",
        intents=[intent]
    )
    
    dict_repr = {
        "schema_version": env.schema_version,
        "event_id": env.event_id,
        "event_type": env.event_type,
        "incident_id": env.incident_id,
        "correlation_id": env.correlation_id,
        "fingerprint": env.fingerprint,
        "created_at": env.created_at,
        "source": {
            "phase": env.source.phase,
            "code_commit": env.source.code_commit
        },
        "problem_summary": env.problem_summary,
        "target_ref": {
            "kind": env.target_ref.kind,
            "canonical_name": env.target_ref.canonical_name
        },
        "phase3_confidence": {
            "score": env.phase3_confidence.score
        },
        "execution_tier": env.execution_tier,
        "safety_violation": env.safety_violation,
        "evidence_refs": env.evidence_refs,
        "intents": [
            {
                "intent_id": i.intent_id,
                "intent_type": i.intent_type,
                "mode": i.mode,
                "target_ref": {
                    "kind": i.target_ref.kind,
                    "canonical_name": i.target_ref.canonical_name
                },
                "parameters": i.parameters,
                "evidence_refs": i.evidence_refs,
                "preconditions": i.preconditions,
                "postconditions": i.postconditions,
                "timeout_seconds": i.timeout_seconds,
                "max_attempts": i.max_attempts,
                "risk_class": i.risk_class,
                "requires_human_approval": i.requires_human_approval
            } for i in env.intents
        ],
        "human_summary": env.human_summary
    }

    is_valid, errors, code = validate_envelope(dict_repr)
    assert is_valid is False
    assert code == ReasonCode.PLACEHOLDER_DETECTED
