"""Strict validation for inbound cross-laptop transport incident events."""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import jsonschema

from transport.contracts import (
    CANONICAL_BLOCKS,
    TransportReasonCode,
    ValidationResult
)
from transport.canonical_json import compute_payload_sha256

SCHEMA_PATH = Path(__file__).parent / "contracts" / "incident_ready_v1.schema.json"
_SCHEMA: Optional[Dict[str, Any]] = None


def _load_schema() -> Dict[str, Any]:
    global _SCHEMA
    if _SCHEMA is None:
        if not SCHEMA_PATH.is_file():
            raise FileNotFoundError(f"Transport schema missing at: {SCHEMA_PATH}")
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA = json.load(f)
    return _SCHEMA


def validate_incident_event(event: Any) -> ValidationResult:
    """
    Validates an incoming cross-laptop incident event against JSON schema and semantic transport contracts.
    Does not mutate the input event.
    Returns: ValidationResult
    """
    if not isinstance(event, dict):
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.REJECTED_SCHEMA,
            errors=["Event root must be a JSON object"]
        )

    # 1. JSON Schema validation
    try:
        schema = _load_schema()
        validator = jsonschema.Draft7Validator(schema)
        errors = [err.message for err in validator.iter_errors(event)]
        if errors:
            return ValidationResult(
                is_valid=False,
                reason_code=TransportReasonCode.REJECTED_SCHEMA,
                errors=errors
            )
    except Exception as e:
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.REJECTED_SCHEMA,
            errors=[f"Schema validation failure: {str(e)}"]
        )

    # 2. schema_version == "1.0"
    if event.get("schema_version") != "1.0":
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.REJECTED_SCHEMA,
            errors=[f"Invalid schema_version: '{event.get('schema_version')}' (expected '1.0')"]
        )

    # 3. event_type == "autosre.incident.ready"
    if event.get("event_type") != "autosre.incident.ready":
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.REJECTED_SCHEMA,
            errors=[f"Invalid event_type: '{event.get('event_type')}' (expected 'autosre.incident.ready')"]
        )

    payload = event.get("payload")
    if not isinstance(payload, dict):
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.REJECTED_SCHEMA,
            errors=["'payload' must be an object"]
        )

    # 4. all 6 canonical blocks present
    missing_blocks = [b for b in CANONICAL_BLOCKS if b not in payload]
    if missing_blocks:
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.MISSING_CANONICAL_BLOCK,
            errors=[f"Payload missing canonical block(s): {', '.join(missing_blocks)}"]
        )

    # 5. payload.incident_event.incident_id exists
    inc_event = payload.get("incident_event")
    if not isinstance(inc_event, dict) or not inc_event.get("incident_id"):
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.MISSING_CANONICAL_BLOCK,
            errors=["payload.incident_event.incident_id is missing or empty"]
        )

    # 6. top-level incident_id == payload.incident_event.incident_id
    top_inc_id = event.get("incident_id")
    payload_inc_id = inc_event.get("incident_id")
    if top_inc_id != payload_inc_id:
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.INCIDENT_ID_MISMATCH,
            errors=[f"Top-level incident_id '{top_inc_id}' does not match payload.incident_event.incident_id '{payload_inc_id}'"]
        )

    # 7. payload hash recomputation matches transport.payload_sha256
    computed_hash = compute_payload_sha256(payload)
    transport_meta = event.get("transport", {})
    expected_hash = transport_meta.get("payload_sha256", "").lower()

    if computed_hash != expected_hash:
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.INVALID_PAYLOAD_HASH,
            errors=[f"Payload hash mismatch: computed '{computed_hash}' != declared '{expected_hash}'"],
            computed_hash=computed_hash
        )

    # 8. event_id non-empty
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.INVALID_EVENT_ID,
            errors=["event_id must be a non-empty string"]
        )

    # 9. correlation_id non-empty
    correlation_id = event.get("correlation_id")
    if not isinstance(correlation_id, str) or not correlation_id.strip():
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.INVALID_CORRELATION_ID,
            errors=["correlation_id must be a non-empty string"]
        )

    # 10. source.engine == "laptop1"
    source = event.get("source", {})
    if not isinstance(source, dict) or source.get("engine") != "laptop1":
        return ValidationResult(
            is_valid=False,
            reason_code=TransportReasonCode.INVALID_SOURCE_ENGINE,
            errors=[f"source.engine must be 'laptop1', got '{source.get('engine') if isinstance(source, dict) else None}'"]
        )

    return ValidationResult(
        is_valid=True,
        reason_code=TransportReasonCode.VALID,
        computed_hash=computed_hash
    )
