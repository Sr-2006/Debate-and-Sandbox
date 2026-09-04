"""Cross-Laptop Transport module for Laptop2."""

from transport.contracts import (
    CANONICAL_BLOCKS,
    EventStatus,
    TransportReasonCode,
    TransportReceipt,
    ValidationResult,
)
from transport.canonical_json import (
    canonical_json_bytes,
    canonical_json_str,
    compute_payload_sha256,
)
from transport.validation import validate_incident_event
from transport.dedup_store import DedupStore
from transport.nats_receiver import (
    Laptop2IncidentReceiver,
    stage_payload_atomically,
)

__all__ = [
    "CANONICAL_BLOCKS",
    "EventStatus",
    "TransportReasonCode",
    "TransportReceipt",
    "ValidationResult",
    "canonical_json_bytes",
    "canonical_json_str",
    "compute_payload_sha256",
    "validate_incident_event",
    "DedupStore",
    "Laptop2IncidentReceiver",
    "stage_payload_atomically",
]
