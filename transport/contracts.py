"""Data structures and contract constants for Laptop2 cross-laptop transport."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional


CANONICAL_BLOCKS = (
    "system_context",
    "incident_event",
    "infrastructure_topology",
    "service_health_status",
    "telemetry_evidence",
    "injected_chaos_context",
)


class TransportReasonCode(str, Enum):
    VALID = "VALID"
    REJECTED_SCHEMA = "REJECTED_SCHEMA"
    MISSING_CANONICAL_BLOCK = "MISSING_CANONICAL_BLOCK"
    INCIDENT_ID_MISMATCH = "INCIDENT_ID_MISMATCH"
    INVALID_PAYLOAD_HASH = "INVALID_PAYLOAD_HASH"
    INVALID_SOURCE_ENGINE = "INVALID_SOURCE_ENGINE"
    INVALID_EVENT_ID = "INVALID_EVENT_ID"
    INVALID_CORRELATION_ID = "INVALID_CORRELATION_ID"


class EventStatus(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    STAGED = "STAGED"
    FAILED = "FAILED"


class ProcessingStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PIPELINE_SUCCEEDED = "PIPELINE_SUCCEEDED"
    RESULT_PUBLISHED = "RESULT_PUBLISHED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    reason_code: TransportReasonCode
    errors: List[str] = field(default_factory=list)
    computed_hash: Optional[str] = None


@dataclass(frozen=True)
class TransportReceipt:
    schema_version: str
    event_type: str
    event_id: str
    parent_event_id: str
    correlation_id: str
    incident_id: str
    status: str
    received_at: str
    laptop2_commit: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "event_id": self.event_id,
            "parent_event_id": self.parent_event_id,
            "correlation_id": self.correlation_id,
            "incident_id": self.incident_id,
            "status": self.status,
            "received_at": self.received_at,
            "laptop2_commit": self.laptop2_commit
        }
