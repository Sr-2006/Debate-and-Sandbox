#!/usr/bin/env python3
"""
shadow_sandbox/reports/event_recorder.py

Canonical Phase 3+4 Event Recorder.
Captures structured, chronologically sequenced, schema-validated execution events
and persists them atomically as phase34_events.jsonl alongside the canonical JSON/MD reports.
"""

import os
import sys
import json
import copy
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from jsonschema import Draft7Validator, FormatChecker

CONTRACTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "contracts"))
SCHEMA_FILE = os.path.join(CONTRACTS_DIR, "phase34_event_v1.schema.json")
REPORTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "reports"))


class EventContractError(ValueError):
    """Raised when an event fails contract schema validation or sequencing rules."""
    pass


class EventWriteError(EventContractError):
    """Raised when atomic event log writing, replacement, or restoration fails."""
    def __init__(self, message: str, backup_paths: Optional[List[str]] = None):
        super().__init__(message)
        self.backup_paths = backup_paths or []


def get_format_checker() -> FormatChecker:
    fc = FormatChecker()

    @fc.checks("date-time")
    def check_datetime(val):
        if not isinstance(val, str):
            return True
        try:
            if "T" not in val:
                return False
            datetime.fromisoformat(val.replace("Z", "+00:00"))
            return True
        except Exception:
            return False

    return fc


def load_event_schema() -> dict:
    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


class Phase34EventRecorder:
    """
    Canonical recorder for Phase 3-4 execution events.
    Enforces continuous sequencing, non-decreasing timestamps, strict Draft7 JSON Schema validation,
    deterministic JSONL serialization, exact file hashing, and atomic persistence.
    """

    def __init__(
        self,
        verification_run_id: str,
        problem_run_id: str,
        case_id: str,
        schema: Optional[dict] = None
    ):
        if not verification_run_id or not str(verification_run_id).strip():
            raise EventContractError("verification_run_id must not be empty")
        if not problem_run_id or not str(problem_run_id).strip():
            raise EventContractError("problem_run_id must not be empty")
        if not case_id or not str(case_id).strip():
            raise EventContractError("case_id must not be empty")

        self.verification_run_id = str(verification_run_id)
        self.problem_run_id = str(problem_run_id)
        self.case_id = str(case_id)
        self.events: List[Dict[str, Any]] = []
        self._schema = schema or load_event_schema()
        self._validator = Draft7Validator(self._schema, format_checker=get_format_checker())
        self._last_timestamp: Optional[datetime] = None

    def record(
        self,
        phase: str,
        component: str,
        event: str,
        status: str,
        reason_code: Optional[str] = None,
        duration_ms: float = 0.0,
        details: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Records a new structured event into memory, validating schema and sequencing invariants.
        """
        now_dt = datetime.now(timezone.utc)
        if timestamp is not None:
            try:
                event_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except Exception as e:
                raise EventContractError(f"Invalid timestamp format: {timestamp}") from e
        else:
            if self._last_timestamp and now_dt < self._last_timestamp:
                event_dt = self._last_timestamp
            else:
                event_dt = now_dt
            timestamp = event_dt.isoformat()

        if self._last_timestamp and event_dt < self._last_timestamp:
            raise EventContractError(
                f"Timestamp violation: event timestamp {timestamp} is earlier than previous {self._last_timestamp.isoformat()}"
            )

        seq = len(self.events) + 1
        dur = max(0.0, float(duration_ms)) if duration_ms is not None else 0.0
        det = details if isinstance(details, dict) else {}

        raw_event = {
            "schema_version": "phase34-event-v1",
            "sequence": seq,
            "timestamp": timestamp,
            "verification_run_id": self.verification_run_id,
            "problem_run_id": self.problem_run_id,
            "case_id": self.case_id,
            "phase": phase,
            "component": component,
            "event": event,
            "status": status,
            "reason_code": reason_code,
            "duration_ms": dur,
            "details": det,
        }

        self.validate_event(raw_event)
        self.events.append(raw_event)
        self._last_timestamp = event_dt
        return raw_event

    def validate_event(self, event: Dict[str, Any]) -> None:
        """Validates an individual event dictionary against schema and basic bounds."""
        errors = sorted(self._validator.iter_errors(event), key=lambda e: (list(e.path), e.message))
        if errors:
            err_msgs = [f"JSON Schema error at {e.json_path}: {e.message}" for e in errors]
            raise EventContractError(f"Event contract validation failed: {err_msgs}")

    def validate_all(self) -> None:
        """
        Validates all recorded events in memory:
        - Each event passes schema.
        - Sequences start at 1 and increment by exactly 1 without gaps.
        - Timestamps are non-decreasing.
        - Run and case identifiers match across all events.
        """
        if not self.events:
            return

        last_dt: Optional[datetime] = None
        for i, ev in enumerate(self.events, start=1):
            if ev.get("sequence") != i:
                raise EventContractError(f"Sequence gap/error at index {i}: got {ev.get('sequence')}")
            if ev.get("verification_run_id") != self.verification_run_id:
                raise EventContractError(f"Mismatched verification_run_id at index {i}")
            if ev.get("problem_run_id") != self.problem_run_id:
                raise EventContractError(f"Mismatched problem_run_id at index {i}")
            if ev.get("case_id") != self.case_id:
                raise EventContractError(f"Mismatched case_id at index {i}")

            self.validate_event(ev)

            ts_str = ev.get("timestamp", "")
            try:
                cur_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception as e:
                raise EventContractError(f"Malformed timestamp '{ts_str}' at sequence {i}") from e

            if last_dt and cur_dt < last_dt:
                raise EventContractError(
                    f"Non-decreasing timestamp violation at sequence {i}: {ts_str} < {last_dt.isoformat()}"
                )
            last_dt = cur_dt

    def serialize_jsonl(self) -> str:
        """
        Serializes all recorded events to deterministic JSONL string format.
        Each event is sorted by key, compactly formatted, and terminated with a newline.
        """
        lines = []
        for ev in self.events:
            line = json.dumps(ev, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            lines.append(line)
        if not lines:
            return ""
        return "\n".join(lines) + "\n"

    def compute_hash(self) -> str:
        """Calculates deterministic SHA-256 hash of the exact UTF-8 bytes of serialized JSONL."""
        serialized = self.serialize_jsonl()
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def write_atomic(self, reports_base_dir: Optional[str] = None) -> str:
        """
        Atomically persists phase34_events.jsonl to destination:
        reports/<verification_run_id>/cases/<case_id>/phase34_events.jsonl
        """
        self.validate_all()
        content = self.serialize_jsonl()

        base_dir = reports_base_dir or REPORTS_DIR
        dest_dir = os.path.join(base_dir, self.verification_run_id, "cases", self.case_id)
        os.makedirs(dest_dir, exist_ok=True)

        dest_file = os.path.join(dest_dir, "phase34_events.jsonl")
        pid_str = f"{os.getpid()}_{uuid.uuid4().hex}"
        tmp_file = os.path.join(dest_dir, f".phase34_events.jsonl.tmp.{pid_str}")
        bak_file = os.path.join(dest_dir, f".phase34_events.jsonl.bak.{pid_str}")

        # Step 1: Write temporary file with flush & fsync
        try:
            with open(tmp_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        except Exception as write_err:
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass
            raise EventWriteError(f"Failed to write temporary event log: {write_err}") from write_err

        # Step 2: Validate the temporary file from disk
        try:
            with open(tmp_file, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f, start=1):
                    line_str = line.strip()
                    if not line_str:
                        continue
                    parsed_event = json.loads(line_str)
                    self.validate_event(parsed_event)
        except Exception as val_err:
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass
            raise EventWriteError(f"Written temporary event log failed disk validation: {val_err}") from val_err

        # Step 3: Backup existing file if present
        backed_up = False
        try:
            if os.path.exists(dest_file):
                os.replace(dest_file, bak_file)
                backed_up = True
        except Exception as bak_err:
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass
            raise EventWriteError(f"Failed to backup existing event log: {bak_err}") from bak_err

        # Step 4: Atomic replacement of destination file
        try:
            os.replace(tmp_file, dest_file)
        except Exception as replace_err:
            restoration_err = None
            if backed_up and os.path.exists(bak_file):
                try:
                    os.replace(bak_file, dest_file)
                except Exception as r_e:
                    restoration_err = r_e

            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass

            if restoration_err is not None:
                raise EventWriteError(
                    f"Event log replacement failed and restoration also failed: {restoration_err}",
                    backup_paths=[bak_file] if os.path.exists(bak_file) else []
                ) from restoration_err

            raise EventWriteError(f"Failed to replace destination event log: {replace_err}") from replace_err

        # Step 5: Clean backup file upon successful replace
        if backed_up and os.path.exists(bak_file):
            try:
                os.remove(bak_file)
            except Exception:
                pass

        return dest_file
