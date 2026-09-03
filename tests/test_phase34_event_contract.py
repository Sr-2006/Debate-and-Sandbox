"""
tests/test_phase34_event_contract.py

Schema and contract test suite for phase34_event_v1.schema.json.
Validates valid events, required fields, constraints, enums, format checking, and additional properties rejection.
"""

import copy
import pytest
from shadow_sandbox.reports.event_recorder import (
    load_event_schema,
    get_format_checker,
    EventContractError,
    Phase34EventRecorder,
)
from jsonschema import Draft7Validator


@pytest.fixture
def event_schema():
    return load_event_schema()


@pytest.fixture
def validator(event_schema):
    return Draft7Validator(event_schema, format_checker=get_format_checker())


@pytest.fixture
def valid_event():
    return {
        "schema_version": "phase34-event-v1",
        "sequence": 1,
        "timestamp": "2026-09-03T12:00:00Z",
        "verification_run_id": "verify_test_01",
        "problem_run_id": "run_test_01",
        "case_id": "case_01",
        "phase": "PHASE_3",
        "component": "agent.optimist",
        "event": "OPTIMIST_COMPLETED",
        "status": "SUCCESS",
        "reason_code": "SUCCESS",
        "duration_ms": 120.5,
        "details": {
            "valid": True,
            "latency_ms": 120.5
        }
    }


def test_valid_event_passes_schema(validator, valid_event):
    errors = list(validator.iter_errors(valid_event))
    assert len(errors) == 0, f"Expected valid event to pass schema: {errors}"


def test_missing_required_property_fails(validator, valid_event):
    for prop in [
        "schema_version",
        "sequence",
        "timestamp",
        "verification_run_id",
        "problem_run_id",
        "case_id",
        "phase",
        "component",
        "event",
        "status",
        "reason_code",
        "duration_ms",
        "details"
    ]:
        ev = copy.deepcopy(valid_event)
        del ev[prop]
        errors = list(validator.iter_errors(ev))
        assert len(errors) > 0, f"Missing required property '{prop}' must fail schema validation"


def test_additional_property_fails(validator, valid_event):
    ev = copy.deepcopy(valid_event)
    ev["extra_field"] = "not_allowed"
    errors = list(validator.iter_errors(ev))
    assert len(errors) > 0, "Additional properties must be rejected"


def test_invalid_phase_enum_fails(validator, valid_event):
    ev = copy.deepcopy(valid_event)
    ev["phase"] = "INVALID_PHASE"
    errors = list(validator.iter_errors(ev))
    assert len(errors) > 0, "Invalid phase enum value must fail schema validation"


def test_invalid_timestamp_format_fails(validator, valid_event):
    for bad_ts in ["not-a-date", "2026-09-03", "2026-09-03 12:00:00", "invalid_iso"]:
        ev = copy.deepcopy(valid_event)
        ev["timestamp"] = bad_ts
        errors = list(validator.iter_errors(ev))
        assert len(errors) > 0, f"Invalid timestamp '{bad_ts}' must fail date-time validation"


def test_negative_duration_fails(validator, valid_event):
    ev = copy.deepcopy(valid_event)
    ev["duration_ms"] = -5.0
    errors = list(validator.iter_errors(ev))
    assert len(errors) > 0, "Negative duration must fail minimum: 0 constraint"


def test_sequence_zero_or_negative_fails(validator, valid_event):
    for bad_seq in [0, -1]:
        ev = copy.deepcopy(valid_event)
        ev["sequence"] = bad_seq
        errors = list(validator.iter_errors(ev))
        assert len(errors) > 0, f"Sequence {bad_seq} must fail minimum: 1 constraint"


def test_schema_version_mismatch_fails(validator, valid_event):
    ev = copy.deepcopy(valid_event)
    ev["schema_version"] = "phase34-event-v2"
    errors = list(validator.iter_errors(ev))
    assert len(errors) > 0, "Non-matching schema_version must fail validation"


def test_nullable_reason_code_accepted(validator, valid_event):
    ev = copy.deepcopy(valid_event)
    ev["reason_code"] = None
    errors = list(validator.iter_errors(ev))
    assert len(errors) == 0, "Null reason_code must be accepted by schema"
