"""NATS JetStream Remediation Result Event Publisher for Laptop2."""

import asyncio
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

import nats
from jsonschema import Draft7Validator

# Ensure Arse_shadow is available for canonical report hashing
_arse_shadow = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Arse_shadow"))
if _arse_shadow not in sys.path:
    sys.path.insert(0, _arse_shadow)

from contracts.models import get_runtime_git_commit
from shadow_sandbox.reports.report_generator import compute_report_hash
from transport.contracts import ValidationResult, TransportReasonCode
from transport.canonical_json import canonical_json_bytes
from transport.dedup_store import DedupStore

DEFAULT_NATS_URL = os.environ.get("AUTOSRE_NATS_URL", "nats://127.0.0.1:4222")
DEFAULT_STREAM = os.environ.get("AUTOSRE_STREAM", "AUTOSRE")
DEFAULT_RESULT_SUBJECT = os.environ.get("AUTOSRE_RESULT_SUBJECT", "autosre.phase34.completed.v1")
DEFAULT_STATE_DB = "runtime/transport.db"

SCHEMA_PATH = Path(__file__).parent / "contracts" / "phase34_completed_v1.schema.json"


def load_completed_schema() -> Dict[str, Any]:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_SCHEMA = load_completed_schema()
_VALIDATOR = Draft7Validator(_SCHEMA)


def validate_completed_event(event: Dict[str, Any]) -> ValidationResult:
    """Validates an outbound phase34.completed event against its JSON schema."""
    if not isinstance(event, dict):
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.REJECTED_SCHEMA,
            errors=[f"Expected dictionary, got {type(event).__name__}"]
        )

    errors = [e.message for e in _VALIDATOR.iter_errors(event)]
    if errors:
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.REJECTED_SCHEMA,
            errors=errors
        )

    return ValidationResult(
        is_valid=True,
        reason_code=TransportReasonCode.VALID,
        errors=[]
    )


def compute_event_log_hash_for_report(
    report: Dict[str, Any],
    report_path: Optional[str] = None
) -> str:
    """
    Computes or retrieves the SHA-256 hash of the corresponding phase34_events.jsonl.
    Fails explicitly if the event log is missing; never silently hashes b"" for a missing file.
    """
    if report_path:
        events_path = os.path.join(os.path.dirname(report_path), "phase34_events.jsonl")
        if os.path.exists(events_path):
            data = open(events_path, "rb").read()
            return hashlib.sha256(data).hexdigest()

    # Fallback to report integrity if present and non-empty
    stored_event_log_hash = report.get("integrity", {}).get("event_log_hash")
    if stored_event_log_hash and len(stored_event_log_hash) == 64:
        return stored_event_log_hash

    raise ValueError(
        "Event log file phase34_events.jsonl is missing and no event_log_hash found in report. "
        "Cannot construct result event without a verified event log."
    )


def build_phase34_completed_event(
    report: Dict[str, Any],
    parent_event_id: str,
    correlation_id: str,
    input_payload_sha256: str,
    report_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Builds a canonical, schema-compliant phase34.completed result event from a phase34_report dictionary.
    Guarantees exact semantic projection and cryptographic integrity without exposing raw chain-of-thought.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    git_sha = get_runtime_git_commit()
    event_id = f"evt_{uuid.uuid4().hex}"

    prob = report.get("problem", {})
    incident_id = (
        prob.get("case_id")
        or report.get("incident_id")
        or prob.get("raw_input", {}).get("incident_id")
        or "unknown_incident"
    )

    final_summary = report.get("final_summary", {})
    phase_4 = report.get("phase_4", {})
    final_outcome = final_summary.get("outcome") or phase_4.get("status", "UNKNOWN")

    # Phase 3 exact projection
    phase_3 = report.get("phase_3", {})
    confidence_obj = phase_3.get("confidence")
    if isinstance(confidence_obj, dict):
        conf_score = confidence_obj.get("score")
    else:
        conf_score = phase_3.get("confidence_score")

    safety_obj = phase_3.get("safety")
    if isinstance(safety_obj, dict):
        safety_status = safety_obj.get("status", "UNKNOWN")
    else:
        safety_status = phase_3.get("safety_status", "UNKNOWN")

    selected_intent = phase_3.get("selected_intent")
    if selected_intent and isinstance(selected_intent, dict):
        clean_intent = {
            "intent_type": selected_intent.get("intent_type"),
            "mode": selected_intent.get("mode"),
            "target_ref": selected_intent.get("target_ref"),
            "parameters": selected_intent.get("parameters", {})
        }
    else:
        clean_intent = None

    # RL Advisory exact projection
    rl = report.get("rl_advisory", {})
    rl_status = rl.get("status", "NOT_RUN")
    rl_mode = rl.get("operating_mode", "SHADOW")
    advisory_dec = rl.get("advisory_decision") or rl.get("recommendation")
    advisory_conf = rl.get("advisory_confidence")
    if advisory_conf is None and advisory_dec and "action_scores" in rl:
        advisory_conf = rl.get("action_scores", {}).get(advisory_dec)
    if advisory_conf is None and "uncertainty" in rl:
        advisory_conf = round(1.0 - float(rl.get("uncertainty", 0.0)), 4)

    rl_feature_schema = rl.get("feature_schema_version", "features-v2") or "features-v2"
    rl_feature_hash = rl.get("feature_hash", "")

    # Phase 4 exact projection
    exec_dict = phase_4.get("execution", {}) if isinstance(phase_4.get("execution"), dict) else {}
    ver_dict = phase_4.get("verification", {}) if isinstance(phase_4.get("verification"), dict) else {}
    rb_dict = phase_4.get("rollback", {}) if isinstance(phase_4.get("rollback"), dict) else {}
    attest_dict = phase_4.get("attestation", {}) if isinstance(phase_4.get("attestation"), dict) else {}

    # Integrity verification
    computed_report_hash = compute_report_hash(report)
    stored_report_hash = report.get("integrity", {}).get("report_hash")
    if stored_report_hash and stored_report_hash != computed_report_hash:
        raise ValueError(
            f"Report integrity mismatch: stored={stored_report_hash} != computed={computed_report_hash}"
        )
    report_hash = computed_report_hash

    event_log_hash = compute_event_log_hash_for_report(report, report_path=report_path)

    if not input_payload_sha256 or len(input_payload_sha256) != 64:
        raise ValueError(f"Valid 64-char hex input_payload_sha256 is required, got: {input_payload_sha256}")

    event = {
        "schema_version": "v1",
        "event_id": event_id,
        "event_type": "autosre.phase34.completed",
        "incident_id": incident_id,
        "parent_event_id": parent_event_id,
        "correlation_id": correlation_id,
        "created_at": now_iso,
        "source": {
            "engine": "laptop2",
            "git_sha": git_sha,
            "generated_at": now_iso
        },
        "final_outcome": str(final_outcome),
        "phase_3": {
            "status": str(phase_3.get("status", "NOT_RUN")),
            "confidence_score": conf_score,
            "orchestrator_decision": str(phase_3.get("orchestrator_decision", "UNKNOWN")),
            "safety_status": str(safety_status),
            "selected_intent": clean_intent
        },
        "rl_advisory": {
            "status": str(rl_status),
            "operating_mode": str(rl_mode),
            "advisory_decision": str(advisory_dec) if advisory_dec is not None else None,
            "advisory_confidence": float(advisory_conf) if advisory_conf is not None else None,
            "feature_schema_version": str(rl_feature_schema),
            "feature_hash": str(rl_feature_hash)
        },
        "phase_4": {
            "status": str(phase_4.get("status", "NOT_RUN")),
            "target": phase_4.get("target"),
            "attestation_status": str(attest_dict.get("status", "NOT_RUN")),
            "execution_status": str(exec_dict.get("status", "NOT_RUN")),
            "execution_capability": str(exec_dict.get("capability", "NOT_RUN")),
            "verification_status": str(ver_dict.get("status", "NOT_RUN")),
            "rollback_status": str(rb_dict.get("status", "NOT_RUN"))
        },
        "integrity": {
            "input_payload_sha256": str(input_payload_sha256),
            "report_hash": str(report_hash),
            "events_log_hash": str(event_log_hash)
        }
    }

    return event


class Laptop2ResultPublisher:
    """Publishes remediation results to NATS JetStream and records delivery state with semantic deduplication."""

    def __init__(
        self,
        nats_url: str = DEFAULT_NATS_URL,
        stream_name: str = DEFAULT_STREAM,
        subject: str = DEFAULT_RESULT_SUBJECT,
        state_db_path: str = DEFAULT_STATE_DB
    ):
        self.nats_url = nats_url
        self.stream_name = stream_name
        self.subject = subject
        self.dedup_store = DedupStore(state_db_path)
        self.nc: Optional[nats.NATS] = None
        self.js = None

    async def connect(self) -> None:
        """Connects to NATS cluster and initializes JetStream context."""
        self.nc = await nats.connect(self.nats_url)
        self.js = self.nc.jetstream()

    async def publish_result(
        self,
        event: Dict[str, Any],
        report_path: Optional[str] = None,
        timeout: float = 10.0
    ) -> Dict[str, Any]:
        """Validates and publishes a result event over JetStream with acknowledgment and restart deduplication."""
        validation = validate_completed_event(event)
        if not validation.is_valid:
            raise ValueError(f"Result event schema validation failed: {validation.errors}")

        parent_event_id = event["parent_event_id"]
        report_hash = event["integrity"]["report_hash"]
        event_type = event.get("event_type", "autosre.phase34.completed")

        # Semantic Deduplication Check
        existing = self.dedup_store.find_published_by_semantic_key(
            parent_event_id=parent_event_id,
            report_hash=report_hash,
            event_type=event_type
        )
        if existing:
            return {
                "status": "SKIPPED_ALREADY_PUBLISHED",
                "subject": self.subject,
                "stream": self.stream_name,
                "seq": existing.get("stream_seq"),
                "event_id": existing.get("event_id"),
                "parent_event_id": parent_event_id,
                "correlation_id": event["correlation_id"],
                "incident_id": event["incident_id"],
                "final_outcome": event["final_outcome"],
                "report_hash": report_hash
            }

        if self.js is None:
            await self.connect()

        payload_bytes = canonical_json_bytes(event)
        ack = await self.js.publish(
            self.subject,
            payload_bytes,
            timeout=timeout
        )

        stream_seq = ack.seq if hasattr(ack, "seq") else None

        # Record publication in dedup store only after JetStream PubAck
        self.dedup_store.record_published(
            event_id=event["event_id"],
            parent_event_id=parent_event_id,
            correlation_id=event["correlation_id"],
            incident_id=event["incident_id"],
            final_outcome=event["final_outcome"],
            report_hash=report_hash,
            event_type=event_type,
            stream_seq=stream_seq,
            report_path=report_path
        )

        return {
            "status": "PUBLISHED",
            "subject": self.subject,
            "stream": ack.stream if hasattr(ack, "stream") else self.stream_name,
            "seq": stream_seq,
            "event_id": event["event_id"],
            "parent_event_id": parent_event_id,
            "correlation_id": event["correlation_id"],
            "incident_id": event["incident_id"],
            "final_outcome": event["final_outcome"],
            "report_hash": report_hash
        }

    async def close(self) -> None:
        """Closes NATS connection safely."""
        if self.nc and not self.nc.is_closed:
            try:
                await self.nc.drain()
            except Exception:
                await self.nc.close()
