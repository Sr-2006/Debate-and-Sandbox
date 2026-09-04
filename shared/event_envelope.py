"""Standard 15-Field Event Envelope for AutoSRE Cross-Laptop Pipeline."""

import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

SCHEMA_VERSION = "1.0.0"

_CACHED_COMMIT_SHA: Optional[str] = None


def get_git_commit_sha() -> str:
    """Returns the current git HEAD commit SHA, or 'unknown_commit'."""
    global _CACHED_COMMIT_SHA
    if _CACHED_COMMIT_SHA is not None:
        return _CACHED_COMMIT_SHA
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False
        )
        if res.returncode == 0 and res.stdout.strip():
            _CACHED_COMMIT_SHA = res.stdout.strip()
            return _CACHED_COMMIT_SHA
    except Exception:
        pass
    _CACHED_COMMIT_SHA = "unknown_commit"
    return _CACHED_COMMIT_SHA


def canonical_json_str(data: Any) -> str:
    """Serializes data into canonical deterministic JSON string."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_json_bytes(data: Any) -> bytes:
    """Serializes data into canonical UTF-8 encoded deterministic JSON bytes."""
    return canonical_json_str(data).encode("utf-8")


def compute_sha256(data: Any) -> str:
    """Computes SHA-256 hex digest over canonical UTF-8 JSON serialization."""
    return hashlib.sha256(canonical_json_bytes(data)).hexdigest()


def build_event_envelope(
    event_type: str,
    root_event_id: str,
    parent_event_id: Optional[str],
    correlation_id: str,
    incident_id: str,
    phase: str,
    component: str,
    status: str,
    payload: Dict[str, Any],
    metrics: Optional[Dict[str, Any]] = None,
    source_engine: str = "laptop2",
    event_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Constructs a standardized 15-field AutoSRE event envelope.
    Ensures root_event_id, correlation_id, and incident_id are preserved.
    Computes canonical integrity SHA-256 over payload.
    """
    if not event_id:
        event_id = f"evt_{uuid.uuid4().hex}"

    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()

    payload_dict = payload if isinstance(payload, dict) else {"data": payload}
    payload_hash = compute_sha256(payload_dict)

    source_block = {
        "engine": source_engine,
        "host": "laptop2",
        "version": "1.0.0"
    }

    integrity_block = {
        "payload_sha256": payload_hash,
        "signature": None,
        "commit_sha": get_git_commit_sha()
    }

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "parent_event_id": parent_event_id,
        "root_event_id": root_event_id,
        "correlation_id": correlation_id,
        "incident_id": incident_id,
        "phase": phase,
        "component": component,
        "event_type": event_type,
        "status": status,
        "timestamp": timestamp,
        "source": source_block,
        "payload": payload_dict,
        "metrics": metrics or {},
        "integrity": integrity_block
    }

    return envelope


REQUIRED_FIELDS = [
    "schema_version",
    "event_id",
    "parent_event_id",
    "root_event_id",
    "correlation_id",
    "incident_id",
    "phase",
    "component",
    "event_type",
    "status",
    "timestamp",
    "source",
    "payload",
    "metrics",
    "integrity"
]


def validate_event_envelope(envelope: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validates presence and format of required 15 fields and integrity hash."""
    if not isinstance(envelope, dict):
        return False, "Envelope must be a JSON dictionary"

    for field in REQUIRED_FIELDS:
        if field not in envelope:
            return False, f"Missing required field: '{field}'"

    if not envelope.get("event_id"):
        return False, "Field 'event_id' cannot be empty"

    if not envelope.get("root_event_id"):
        return False, "Field 'root_event_id' cannot be empty"

    if not envelope.get("correlation_id"):
        return False, "Field 'correlation_id' cannot be empty"

    if not envelope.get("incident_id"):
        return False, "Field 'incident_id' cannot be empty"

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return False, "Field 'payload' must be a dictionary"

    integrity = envelope.get("integrity")
    if not isinstance(integrity, dict) or "payload_sha256" not in integrity:
        return False, "Field 'integrity' must contain 'payload_sha256'"

    computed_hash = compute_sha256(payload)
    if computed_hash != integrity.get("payload_sha256"):
        return False, f"Payload SHA256 mismatch: computed={computed_hash}, envelope={integrity.get('payload_sha256')}"

    return True, None
