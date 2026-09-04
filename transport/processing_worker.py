"""Laptop 2 Automated Remediation Processing Worker.

Pulls STAGED incident events, validates input payload hash and envelope identity,
executes the Phase 3/4 pipeline coordinator, verifies source file immutability,
validates output report integrity, and publishes completion results back to JetStream.
"""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from transport.contracts import ProcessingStatus
from transport.canonical_json import compute_payload_sha256
from transport.dedup_store import DedupStore
from transport.result_publisher import (
    Laptop2ResultPublisher,
    build_phase34_completed_event,
    compute_event_log_hash_for_report,
    DEFAULT_NATS_URL,
    DEFAULT_STREAM,
    DEFAULT_RESULT_SUBJECT,
    DEFAULT_STATE_DB,
)
from shadow_sandbox.reports.report_generator import compute_report_hash


class Laptop2ProcessingWorker:
    """Automated worker orchestrating STAGED incident -> Pipeline Execution -> Result Publication."""

    def __init__(
        self,
        state_db_path: str = DEFAULT_STATE_DB,
        nats_url: str = DEFAULT_NATS_URL,
        stream_name: str = DEFAULT_STREAM,
        subject: str = DEFAULT_RESULT_SUBJECT,
        pipeline_timeout_seconds: float = 900.0,
        reports_base_dir: Optional[str] = None
    ):
        self.state_db_path = state_db_path
        self.nats_url = nats_url
        self.stream_name = stream_name
        self.subject = subject
        self.pipeline_timeout_seconds = pipeline_timeout_seconds
        self.reports_base_dir = reports_base_dir
        self.dedup_store = DedupStore(self.state_db_path)

    def _verify_staged_input_and_identity(
        self,
        input_path: str,
        expected_parent_event_id: str,
        expected_correlation_id: str,
        expected_incident_id: str,
        expected_payload_hash: str
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str], Optional[Dict[str, Any]]]:
        """
        Validates staged input file existence, raw byte hash before processing,
        envelope identity fields, and canonical payload SHA-256 against received_events.payload_hash.

        Returns: (is_valid, error_code, error_message, raw_file_sha256_before, raw_envelope_dict)
        """
        if not os.path.exists(input_path):
            return False, "INPUT_NOT_FOUND", f"Staged input file not found at: {input_path}", None, None

        try:
            with open(input_path, "rb") as f:
                raw_bytes = f.read()
            raw_sha_before = hashlib.sha256(raw_bytes).hexdigest()
        except Exception as e:
            return False, "INPUT_READ_ERROR", f"Failed reading staged input file: {e}", None, None

        try:
            staged_data = json.loads(raw_bytes.decode("utf-8"))
        except Exception as e:
            return False, "INPUT_JSON_INVALID", f"Staged file contains invalid JSON: {e}", raw_sha_before, None

        if not isinstance(staged_data, dict):
            return False, "INPUT_JSON_INVALID", "Staged file root must be a JSON object", raw_sha_before, None

        # Strict Envelope Identity Validation
        staged_event_id = staged_data.get("event_id")
        if staged_event_id != expected_parent_event_id:
            return (
                False,
                "INPUT_IDENTITY_MISMATCH",
                f"Staged event_id '{staged_event_id}' does not match expected parent_event_id '{expected_parent_event_id}'",
                raw_sha_before,
                staged_data
            )

        staged_corr_id = staged_data.get("correlation_id")
        if staged_corr_id != expected_correlation_id:
            return (
                False,
                "INPUT_IDENTITY_MISMATCH",
                f"Staged correlation_id '{staged_corr_id}' does not match expected correlation_id '{expected_correlation_id}'",
                raw_sha_before,
                staged_data
            )

        staged_inc_id = staged_data.get("incident_id")
        if staged_inc_id != expected_incident_id:
            return (
                False,
                "INPUT_IDENTITY_MISMATCH",
                f"Staged incident_id '{staged_inc_id}' does not match expected incident_id '{expected_incident_id}'",
                raw_sha_before,
                staged_data
            )

        # Extract payload block for canonical hash calculation
        if "payload" in staged_data and isinstance(staged_data["payload"], dict):
            payload = staged_data["payload"]
        else:
            payload = staged_data

        # Verify incident_id in payload incident_event block if present
        payload_inc_id = payload.get("incident_event", {}).get("incident_id") if isinstance(payload.get("incident_event"), dict) else None
        if payload_inc_id and payload_inc_id != expected_incident_id:
            return (
                False,
                "INPUT_IDENTITY_MISMATCH",
                f"Payload incident_id '{payload_inc_id}' does not match expected incident_id '{expected_incident_id}'",
                raw_sha_before,
                staged_data
            )

        # Canonical Payload Hash Assertion
        computed_payload_hash = compute_payload_sha256(payload)
        if computed_payload_hash != expected_payload_hash.lower():
            return (
                False,
                "INPUT_HASH_MISMATCH",
                f"Canonical payload hash mismatch: computed '{computed_payload_hash}' != expected '{expected_payload_hash}'",
                raw_sha_before,
                staged_data
            )

        return True, None, None, raw_sha_before, staged_data

    def _verify_file_immutability(self, input_path: str, raw_sha_before: str) -> Tuple[bool, Optional[str]]:
        """Verifies that the staged input file bytes remained strictly identical before and after processing."""
        if not os.path.exists(input_path):
            return False, f"Staged input file missing after processing: {input_path}"

        try:
            with open(input_path, "rb") as f:
                raw_bytes = f.read()
            raw_sha_after = hashlib.sha256(raw_bytes).hexdigest()
            if raw_sha_after != raw_sha_before:
                return False, f"Staged file mutated during execution: before={raw_sha_before}, after={raw_sha_after}"
            return True, None
        except Exception as e:
            return False, f"Failed verifying staged file immutability: {e}"

    def _run_pipeline_subprocess(self, input_path: str) -> Tuple[int, str, str, Optional[Dict[str, Any]]]:
        """Runs run_mvp_pipeline.py in an isolated subprocess and parses machine-readable summary."""
        cmd = [
            sys.executable,
            "run_mvp_pipeline.py",
            "--input",
            input_path,
            "--json-summary"
        ]
        if self.reports_base_dir:
            cmd.extend(["--reports-dir", self.reports_base_dir])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.pipeline_timeout_seconds,
                check=False
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            returncode = proc.returncode

            # Extract [PIPELINE_RESULT_JSON] block
            summary_dict = None
            match = re.search(r"\[PIPELINE_RESULT_JSON\]\s*(\{.*?\})\s*\[/PIPELINE_RESULT_JSON\]", stdout, re.DOTALL)
            if match:
                try:
                    summary_dict = json.loads(match.group(1))
                except Exception:
                    pass

            # Fallback stdout line parsing if JSON block missing
            if not summary_dict:
                report_match = re.search(r"JSON Report\s*:\s*(\S+)", stdout)
                events_match = re.search(r"Events Log\s*:\s*(\S+)", stdout)
                outcome_match = re.search(r"Final Outcome\s*:\s*(\S+)", stdout)
                incident_match = re.search(r"Incident \[([^\]]+)\] complete", stdout)
                if report_match:
                    summary_dict = {
                        "json_report": report_match.group(1).strip(),
                        "events_report": events_match.group(1).strip() if events_match else None,
                        "outcome": outcome_match.group(1).strip() if outcome_match else "UNKNOWN",
                        "incident_id": incident_match.group(1).strip() if incident_match else None
                    }

            return returncode, stdout, stderr, summary_dict

        except subprocess.TimeoutExpired as te:
            return -1, te.stdout or "", te.stderr or f"TimeoutExpired after {self.pipeline_timeout_seconds}s", None
        except Exception as ex:
            return -2, "", str(ex), None

    async def _publish_result_event(
        self,
        report_data: Dict[str, Any],
        parent_event_id: str,
        correlation_id: str,
        input_payload_hash: str,
        report_path: str
    ) -> Dict[str, Any]:
        """Builds and publishes the completed result event to JetStream."""
        event = build_phase34_completed_event(
            report=report_data,
            parent_event_id=parent_event_id,
            correlation_id=correlation_id,
            input_payload_sha256=input_payload_hash,
            report_path=report_path
        )

        publisher = Laptop2ResultPublisher(
            nats_url=self.nats_url,
            stream_name=self.stream_name,
            subject=self.subject,
            state_db_path=self.state_db_path
        )
        try:
            res = await publisher.publish_result(event, report_path=report_path)
            return {"publish_result": res, "event": event}
        finally:
            await publisher.close()

    async def process_event_async(
        self,
        parent_event_id: Optional[str] = None,
        retry_failed: bool = False,
        recover_stale: bool = False
    ) -> Dict[str, Any]:
        """Claims a STAGED incident event, executes the pipeline, and publishes the result."""
        claim = self.dedup_store.claim_staged_event(
            parent_event_id=parent_event_id,
            retry_failed=retry_failed,
            recover_stale=recover_stale
        )

        if not claim:
            return {
                "status": "NO_STAGED_EVENTS",
                "parent_event_id": parent_event_id,
                "message": "No eligible STAGED event available for claiming."
            }

        claim_status = claim.get("status")
        if claim_status in ["ALREADY_COMPLETED", "ALREADY_CLAIMED", "FAILED_REQUIRES_RETRY"]:
            return claim

        event_id = claim["parent_event_id"]
        correlation_id = claim["correlation_id"]
        incident_id = claim["incident_id"]
        input_path = claim["input_path"]
        input_payload_hash = claim["input_payload_hash"]

        # Step 1: Input file immutability, envelope identity, and canonical payload hash verification
        is_valid_input, err_code, input_err, raw_sha_before, _ = self._verify_staged_input_and_identity(
            input_path=input_path,
            expected_parent_event_id=event_id,
            expected_correlation_id=correlation_id,
            expected_incident_id=incident_id,
            expected_payload_hash=input_payload_hash
        )
        if not is_valid_input:
            self.dedup_store.mark_processing_failed(event_id, err_code or "INPUT_INVALID", input_err or "Invalid input")
            return {
                "status": "FAILED",
                "error_code": err_code or "INPUT_INVALID",
                "parent_event_id": event_id,
                "message": input_err
            }

        # Step 2: Run pipeline subprocess
        returncode, stdout, stderr, summary_dict = self._run_pipeline_subprocess(input_path)

        # Step 2b: Verify file immutability immediately after subprocess finishes (success or failure)
        is_immutable, immut_err = self._verify_file_immutability(input_path, raw_sha_before)
        if not is_immutable:
            self.dedup_store.mark_processing_failed(event_id, "INPUT_FILE_TAMPERED", immut_err or "File mutated")
            return {
                "status": "FAILED",
                "error_code": "INPUT_FILE_TAMPERED",
                "parent_event_id": event_id,
                "message": immut_err
            }

        if returncode == -1:
            err_msg = f"Pipeline execution timed out after {self.pipeline_timeout_seconds}s"
            self.dedup_store.mark_processing_failed(event_id, "PIPELINE_TIMEOUT", err_msg)
            return {
                "status": "FAILED",
                "error_code": "PIPELINE_TIMEOUT",
                "parent_event_id": event_id,
                "message": err_msg
            }

        if returncode != 0:
            err_msg = f"Pipeline exited with returncode {returncode}: {stderr[:500]}"
            self.dedup_store.mark_processing_failed(event_id, "PIPELINE_EXIT_NONZERO", err_msg)
            return {
                "status": "FAILED",
                "error_code": "PIPELINE_EXIT_NONZERO",
                "parent_event_id": event_id,
                "message": err_msg
            }

        if not summary_dict or not summary_dict.get("json_report"):
            err_msg = "Pipeline completed without generating a readable report path"
            self.dedup_store.mark_processing_failed(event_id, "REPORT_NOT_FOUND", err_msg)
            return {
                "status": "FAILED",
                "error_code": "REPORT_NOT_FOUND",
                "parent_event_id": event_id,
                "message": err_msg
            }

        report_path = summary_dict["json_report"]
        if not os.path.exists(report_path):
            err_msg = f"Generated report file missing at: {report_path}"
            self.dedup_store.mark_processing_failed(event_id, "REPORT_NOT_FOUND", err_msg)
            return {
                "status": "FAILED",
                "error_code": "REPORT_NOT_FOUND",
                "parent_event_id": event_id,
                "message": err_msg
            }

        # Step 3: Load and verify report integrity
        try:
            with open(report_path, "r", encoding="utf-8") as rf:
                report_data = json.load(rf)
        except Exception as e:
            err_msg = f"Failed to load JSON report: {e}"
            self.dedup_store.mark_processing_failed(event_id, "REPORT_LOAD_ERROR", err_msg)
            return {
                "status": "FAILED",
                "error_code": "REPORT_LOAD_ERROR",
                "parent_event_id": event_id,
                "message": err_msg
            }

        # Verify incident correspondence
        report_case_id = (
            report_data.get("problem", {}).get("case_id")
            or report_data.get("incident_id")
        )
        summary_incident_id = summary_dict.get("incident_id")
        if (report_case_id and report_case_id != incident_id) or (summary_incident_id and summary_incident_id != incident_id):
            err_msg = f"Report incident mismatch: expected {incident_id}, found report_case_id={report_case_id}, summary_incident_id={summary_incident_id}"
            self.dedup_store.mark_processing_failed(event_id, "REPORT_MISMATCH", err_msg)
            return {
                "status": "FAILED",
                "error_code": "REPORT_MISMATCH",
                "parent_event_id": event_id,
                "message": err_msg
            }

        computed_report_hash = compute_report_hash(report_data)
        run_id = report_data.get("run", {}).get("verification_run_id") or summary_dict.get("verification_run_id") or "unknown_run"
        final_outcome = report_data.get("final_summary", {}).get("outcome") or summary_dict.get("outcome") or "UNKNOWN"

        self.dedup_store.mark_pipeline_succeeded(
            parent_event_id=event_id,
            pipeline_run_id=run_id,
            report_path=report_path,
            report_hash=computed_report_hash
        )

        # Step 4: Publish result event over JetStream
        try:
            pub_info = await self._publish_result_event(
                report_data=report_data,
                parent_event_id=event_id,
                correlation_id=correlation_id,
                input_payload_hash=input_payload_hash,
                report_path=report_path
            )
        except Exception as pe:
            err_msg = f"Result event publication failed: {pe}"
            self.dedup_store.mark_processing_failed(event_id, "RESULT_PUBLISH_FAILED", err_msg)
            return {
                "status": "FAILED",
                "error_code": "RESULT_PUBLISH_FAILED",
                "parent_event_id": event_id,
                "message": err_msg
            }

        result_event = pub_info["event"]
        pub_result = pub_info["publish_result"]
        result_event_id = result_event["event_id"]

        # Validate semantic dedup return if skipped
        if pub_result.get("status") == "SKIPPED_ALREADY_PUBLISHED":
            if pub_result.get("parent_event_id") != event_id or pub_result.get("report_hash") != computed_report_hash:
                err_msg = f"Semantic dedup returned mismatched publication record: {pub_result}"
                self.dedup_store.mark_processing_failed(event_id, "RESULT_DEDUP_MISMATCH", err_msg)
                return {
                    "status": "FAILED",
                    "error_code": "RESULT_DEDUP_MISMATCH",
                    "parent_event_id": event_id,
                    "message": err_msg
                }
            result_event_id = pub_result.get("event_id")

        # Step 5: Mark RESULT_PUBLISHED only after successful publication / verified dedup
        self.dedup_store.mark_result_published(
            parent_event_id=event_id,
            result_event_id=result_event_id,
            report_hash=computed_report_hash
        )

        return {
            "status": "PROCESSING_COMPLETE",
            "parent_event_id": event_id,
            "correlation_id": correlation_id,
            "incident_id": incident_id,
            "pipeline_run_id": run_id,
            "final_outcome": final_outcome,
            "report_path": report_path,
            "report_hash": computed_report_hash,
            "result_event_id": result_event_id,
            "publish_status": pub_result.get("status")
        }

    def process_event(
        self,
        parent_event_id: Optional[str] = None,
        retry_failed: bool = False,
        recover_stale: bool = False
    ) -> Dict[str, Any]:
        """Synchronous wrapper for process_event_async."""
        return asyncio.run(
            self.process_event_async(
                parent_event_id=parent_event_id,
                retry_failed=retry_failed,
                recover_stale=recover_stale
            )
        )
